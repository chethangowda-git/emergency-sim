"""Protocols beyond admission (DESIGN 5.3 to 5.6), mixed into AgentPolicy: hospital-side award
handling, holds on pre-alerts, routine stabilize-and-transfer, staff lending, mutual-aid vehicles
and the swap. Multi-party requests collect replies for one round; a missing reply is a decline."""
from __future__ import annotations

from typing import TYPE_CHECKING

from coordination.schemas import (LoanResponse, ProposedAction, StaffRequest, TransferRequest, TransferResponse,
                                  VehicleRequest)
from coordination.tools import DispatchScope, HospitalScope, staff_free_slots
from sim.actions import LendStaff, LendVehicle, Reserve, TransferPatient

if TYPE_CHECKING:
    from coordination.messages import Message
    from coordination.orchestrator import AgentPolicy

LOAN_MINUTES = 90
STAFF_COOLDOWN = 30


class HospitalProtocols:
    # ----- admission award at the hospital -----

    def _h_admission_admission_award(self: AgentPolicy, m: Message) -> None:
        h = m.recipient
        scope = HospitalScope(self.view, h)
        ready = {p["id"] for p in scope.patients("ready_for_release")} - self.minute_released
        releases = [a for a in map(ProposedAction.model_validate, m.proposed_action["actions"])
                    if a.action != "release_patient" or a.victim in ready]
        self.minute_released |= {a.victim for a in releases if a.action == "release_patient"}
        victims = m.proposed_action["victims"]
        if m.proposed_action["conditional"]:
            self.act(*releases)
            self.pending_reserve[h] = {"thread": m.thread_id, "victims": victims, "round": m.round}
            self._consider_transfer(h, None, forced=True)
            return
        held = sum(1 for x in scope.me["holds"] if x["victim"] in victims)
        room = scope.me["free_slots"] + held + len([a for a in releases if a.action == "release_patient"])
        if room - self.minute_reserved.get(h, 0) < len(victims):
            self.send(h, "dispatch", "decline", m.thread_id, m.round, victim_ids=victims,
                      reason="capacity taken since my offer")
            return
        self.act(*releases)
        self._reserve_and_ack(h, m.thread_id, m.round, victims)

    def _reserve_and_ack(self: AgentPolicy, h: str, thread: str, round_: int, victims: list[str]) -> None:
        self.minute_reserved[h] = self.minute_reserved.get(h, 0) + len(victims)
        self.act(Reserve(h, tuple(victims), "firm", by=h))
        self.send(h, "dispatch", "ack", thread, round_, victim_ids=victims, reason="firm reservation placed")

    def _after_transfer(self: AgentPolicy, hospital: str) -> None:
        pending = self.pending_reserve.pop(hospital, None)
        if pending:
            self._reserve_and_ack(hospital, pending["thread"], pending["round"], pending["victims"])

    # ----- holds on pre-alert (DESIGN 3.8) -----

    def _pre_alert(self: AgentPolicy, iid: str) -> None:
        inc = self._incident(iid)
        scope = DispatchScope(self.view)
        for v in inc["victims"]:
            if v["severity"] != "critical" or v["status"] != "waiting" or v["id"] in self._claimed():
                continue
            capable = [h for h in scope.hospitals() if v["care_need"] in h["capabilities"]]
            if not capable or not self._cool("pre_alert", v["id"]):
                continue
            nearest = min(capable, key=lambda h: (scope.travel(inc["node"], h["node"]), h["id"]))
            th = self.open_thread("prealert", victim=v["id"])
            self.send("dispatch", nearest["id"], "pre_alert", th, incident_id=iid, victim_ids=[v["id"]],
                      reason="critical waiting for a unit")

    def _h_prealert_pre_alert(self: AgentPolicy, m: Message) -> None:
        h, vid = m.recipient, m.victim_ids[0]
        scope = HospitalScope(self.view, h)
        need = self._victim(vid)["care_need"]
        if self.negotiate and scope.mood() != "eager" and scope.real_capacity(need) > 0:
            self.act(Reserve(h, (vid,), "hold", by=h))
            self.decision(h, "hold", "rule", f"holding a slot for expected critical {vid}", victim=vid)

    # ----- routine stabilize and transfer (DESIGN 5.5) -----

    def _consider_transfer(self: AgentPolicy, hospital: str, victim: str | None, forced: bool = False) -> None:
        if not self.negotiate:
            return
        scope = HospitalScope(self.view, hospital)
        if not forced and scope.mood() != "protective":
            return
        candidates = [p for p in scope.patients("transferable") if victim is None or p["id"] == victim]
        if not candidates or not scope.idle_vehicles() or not self._cool("transfer", hospital, 10):
            if forced:
                self._transfer_failed(hospital)
            return
        p = candidates[0]
        others = [h for h in DispatchScope(self.view).hospitals() if h["id"] != hospital]
        th = self.open_thread("transfer", sender=hospital, victim=p["id"], replies={},
                              expected=[h["id"] for h in others], due=self.t + 2)
        req = TransferRequest(victim=p["id"], care_need=p["care_need"], from_hospital=hospital,
                              sender_capabilities=scope.me["capabilities"])
        for h in others:
            self.send(hospital, h["id"], "transfer_request", th, victim_ids=[p["id"]], proposed_action=req.model_dump())

    def _transfer_failed(self: AgentPolicy, hospital: str) -> None:
        pending = self.pending_reserve.pop(hospital, None)
        if pending:
            self.send(hospital, "dispatch", "decline", pending["thread"], pending["round"],
                      victim_ids=pending["victims"], reason="transfer did not happen")

    def _h_transfer_transfer_request(self: AgentPolicy, m: Message) -> None:
        r = self.hospital_agents[m.recipient].respond_transfer(HospitalScope(self.view, m.recipient),
                                                               TransferRequest.model_validate(m.proposed_action))
        self.decision(m.recipient, "transfer_response", r.decided_by, r.reason, accept=r.accept, victim=m.victim_ids[0])
        self.send(m.recipient, m.sender, "transfer_response", m.thread_id, victim_ids=m.victim_ids,
                  proposed_action=r.model_dump(), reason=r.reason, decided_by=r.decided_by)

    def _h_transfer_transfer_response(self: AgentPolicy, m: Message) -> None:
        th = self.threads[m.thread_id]
        th["replies"][m.sender] = TransferResponse.model_validate(m.proposed_action)
        if set(th["replies"]) >= set(th["expected"]):
            self._choose_receiver(m.thread_id)

    def _choose_receiver(self: AgentPolicy, thread: str) -> None:
        th = self.threads[thread]
        th["due"] = None
        scope = DispatchScope(self.view)
        node = {h["id"]: h["node"] for h in scope.hospitals()}
        yes = sorted((h for h, r in th["replies"].items() if r.accept),
                     key=lambda h: (scope.travel(node[th["sender"]], node[h]), h))
        if not yes:
            self._transfer_failed(th["sender"])
            return
        th["receiver"] = yes[0]
        self.send(th["sender"], yes[0], "ack", thread, victim_ids=[th["victim"]], reason="you take the transfer")

    def _h_transfer_ack(self: AgentPolicy, m: Message) -> None:
        th = self.threads[m.thread_id]
        if m.recipient == th["receiver"]:
            self._reserve_and_ack(m.recipient, m.thread_id, m.round, [th["victim"]])
            return
        vehicles = HospitalScope(self.view, th["sender"]).idle_vehicles()
        if not vehicles:
            self._transfer_failed(th["sender"])
            return
        self.act(TransferPatient(th["victim"], th["receiver"], vehicles[0], by=th["sender"]))
        self.decision(th["sender"], "transfer", "rule", f"transfer {th['victim']} to {th['receiver']}")

    # ----- staff lending (DESIGN 5.6) -----

    def _hospital_initiatives(self: AgentPolicy) -> None:
        if not self.negotiate:
            self._collect_timeouts()
            return
        for h in self.view.data["hospitals"]:
            waiting = [p for p in h["patients"] if p["status"] == "in_er"]
            if not waiting or not self._cool("staff", h["id"], STAFF_COOLDOWN):
                continue
            need = waiting[0]["care_need"]
            kind = "doctor" if staff_free_slots(h["doctors"], need) == 0 else "nurse"
            others = [x["id"] for x in self.view.data["hospitals"] if x["id"] != h["id"]]
            th = self.open_thread("staff", borrower=h["id"], replies={}, expected=others, due=self.t + 2)
            req = StaffRequest(from_hospital=h["id"], kind=kind, skill=need, minutes=LOAN_MINUTES)
            for o in others:
                self.send(h["id"], o, "staff_request", th, proposed_action=req.model_dump(),
                          reason=f"{len(waiting)} patient(s) waiting for a {kind}")
        self._collect_timeouts()

    def _h_staff_staff_request(self: AgentPolicy, m: Message) -> None:
        r = self.hospital_agents[m.recipient].respond_staff(HospitalScope(self.view, m.recipient),
                                                            StaffRequest.model_validate(m.proposed_action))
        self.decision(m.recipient, "staff_offer", r.decided_by, r.reason, answer=r.answer)
        self.send(m.recipient, m.sender, "staff_offer", m.thread_id, proposed_action=r.model_dump(), reason=r.reason,
                  decided_by=r.decided_by)

    def _h_staff_staff_offer(self: AgentPolicy, m: Message) -> None:
        th = self.threads[m.thread_id]
        th["replies"][m.sender] = LoanResponse.model_validate(m.proposed_action)
        if set(th["replies"]) >= set(th["expected"]):
            self._choose_loan(m.thread_id, th["borrower"])

    def _choose_loan(self: AgentPolicy, thread: str, requester: str) -> None:
        th = self.threads[thread]
        th["due"] = None
        scope = DispatchScope(self.view)
        node = {h["id"]: h["node"] for h in scope.hospitals()}
        here = node.get(requester) or self._incident(requester)["node"]
        yes = sorted((h for h, r in th["replies"].items() if r.answer == "send_now"),
                     key=lambda h: (scope.travel(node[h], here), h))
        th["chosen"] = yes[0] if yes else None
        if yes:
            self.send(requester if requester in node else "dispatch", yes[0], "ack", thread, reason="offer accepted")
        elif th["protocol"] == "swap":
            self._redirect(th["unit"], th["incident"])

    def _h_staff_ack(self: AgentPolicy, m: Message) -> None:
        offer = self.threads[m.thread_id]["replies"][m.recipient]
        self.act(*[a for a in offer.proposed_actions])

    # ----- mutual aid vehicles and the swap (DESIGN 5.3, 5.4) -----

    def _ask_vehicle(self: AgentPolicy, protocol: str, iid: str, unit: str | None = None) -> None:
        hospitals = [h["id"] for h in DispatchScope(self.view).hospitals()]
        th = self.open_thread(protocol, incident=iid, unit=unit, replies={}, expected=hospitals, due=self.t + 2)
        kind = "mutual_aid_request" if protocol == "mutual_aid" else "swap_request"
        req = VehicleRequest(incident=iid, until=self.t + LOAN_MINUTES)
        for h in hospitals:
            self.send("dispatch", h, kind, th, incident_id=iid, proposed_action=req.model_dump(),
                      reason="need a vehicle for non-critical victims" if protocol == "mutual_aid"
                      else f"take over {unit}'s non-critical job so it can go to a critical")

    def _vehicle_request(self: AgentPolicy, m: Message, reply: str) -> None:
        r = self.hospital_agents[m.recipient].respond_vehicle(HospitalScope(self.view, m.recipient),
                                                              VehicleRequest.model_validate(m.proposed_action))
        if not self.dispatch_negotiates:
            r = r.model_copy(update={"answer": "decline", "reason": "no mutual aid", "proposed_actions": []})
        self.decision(m.recipient, reply, r.decided_by, r.reason, answer=r.answer)
        self.send(m.recipient, "dispatch", reply, m.thread_id, incident_id=m.incident_id,
                  proposed_action=r.model_dump(), reason=r.reason, decided_by=r.decided_by)

    def _h_mutual_aid_mutual_aid_request(self: AgentPolicy, m: Message) -> None:
        self._vehicle_request(m, "mutual_aid_offer")

    def _h_swap_swap_request(self: AgentPolicy, m: Message) -> None:
        self._vehicle_request(m, "swap_response")

    def _vehicle_reply(self: AgentPolicy, m: Message) -> None:
        th = self.threads[m.thread_id]
        th["replies"][m.sender] = LoanResponse.model_validate(m.proposed_action)
        if set(th["replies"]) >= set(th["expected"]):
            self._choose_vehicle(m.thread_id)

    _h_mutual_aid_mutual_aid_offer = _vehicle_reply
    _h_swap_swap_response = _vehicle_reply

    def _choose_vehicle(self: AgentPolicy, thread: str) -> None:
        th = self.threads[thread]
        self._choose_loan(thread, th["incident"])
        if th["protocol"] == "swap" and th.get("chosen"):
            self._redirect(th["unit"], th["incident"])

    def _h_vehicle_ack(self: AgentPolicy, m: Message) -> None:
        offer = self.threads[m.thread_id]["replies"][m.recipient]
        self.act(*offer.proposed_actions)

    _h_mutual_aid_ack = _h_vehicle_ack
    _h_swap_ack = _h_vehicle_ack

    # ----- one-round timeouts -----

    def _collect_timeouts(self: AgentPolicy) -> None:
        """A request whose replies are not all in after one round treats the missing ones as declines."""
        for tid, th in list(self.threads.items()):
            if th.get("due") is None or self.t < th["due"]:
                continue
            if th["protocol"] == "transfer":
                self._choose_receiver(tid)
            elif th["protocol"] == "staff":
                self._choose_loan(tid, th["borrower"])
            elif th["protocol"] in ("mutual_aid", "swap"):
                th["due"] = None
                self._choose_vehicle(tid)
