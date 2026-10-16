----------------------- MODULE AuditDisplayCorrections -----------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANT Fault
Targets == {"A", "B"}
Operations == 1..3
VARIABLES selected, visible, high, permission, editable, remote, requests
vars == <<selected, visible, high, permission, editable, remote, requests>>

(* Focused UI obligation for #163 D8 / #167, not a persistence proof.
   A/B are distinct task+round+file-version tuples. Three operations permit
   overlap across files and a second revision. Server CAS is an assumption.
   Invocation, external write, and observation are separate; non-response is
   allowed forever (stuttering), with no fairness or liveness claim.
   Raw field text, frozen audit data, restart persistence, and real permission
   enforcement are outside this model; DTO/browser tests cover UI isolation.
   Each Fault exposes one forbidden outcome, not a production transition. *)
Init ==
  /\ selected = "A"
  /\ visible = [t \in Targets |-> [origin |-> t, revision |-> 0]]
  /\ high = [t \in Targets |-> 0]
  /\ permission = [t \in Targets |-> TRUE]
  /\ editable = permission
  /\ remote = [t \in Targets |-> 0]
  /\ requests = [i \in Operations |-> [target |-> "A", expected |-> 0,
       phase |-> "unused", effect |-> "none"]]

Unresolved(t) == {i \in Operations : requests[i].target = t /\
                                  requests[i].phase \in {"pending", "unknown"}}
Select(t) == /\ selected' = t
             /\ UNCHANGED <<visible, high, permission, editable, remote, requests>>
Start(t, i) ==
  /\ requests[i].phase = "unused" /\ editable[t]
  /\ (Unresolved(t) = {} \/ Fault = "resend")
  /\ visible[t].revision < 2
  /\ requests' = [requests EXCEPT ![i] = [target |-> t,
       expected |-> visible[t].revision, phase |-> "pending", effect |-> "none"]]
  /\ UNCHANGED <<selected, visible, high, permission, editable, remote>>
Write(i) ==
  LET r == requests[i] IN
  /\ r.phase \in {"pending", "unknown"} /\ r.effect = "none"
  /\ IF r.expected = remote[r.target]
        THEN /\ remote' = [remote EXCEPT ![r.target] = @ + 1]
             /\ requests' = [requests EXCEPT ![i].effect = "written"]
        ELSE /\ requests' = [requests EXCEPT ![i].effect = "rejected"]
             /\ UNCHANGED remote
  /\ UNCHANGED <<selected, visible, high, permission, editable>>
LoseObservation(i) ==
  /\ requests[i].phase = "pending"
  /\ requests' = [requests EXCEPT ![i].phase = "unknown"]
  /\ UNCHANGED <<selected, visible, high, permission, editable, remote>>
Receive(i) ==
  LET r == requests[i]
      target == IF Fault = "selected" THEN selected ELSE r.target
      revision == r.expected + 1
  IN /\ r.phase \in {"pending", "unknown"} /\ r.effect # "none"
     /\ requests' = [requests EXCEPT ![i].phase =
                        IF r.effect = "written" THEN "saved" ELSE "rejected"]
     /\ IF r.effect = "written"
           THEN /\ visible' = IF revision > visible[target].revision
                              THEN [visible EXCEPT ![target] =
                                    [origin |-> r.target, revision |-> revision]]
                              ELSE visible
                /\ high' = [high EXCEPT ![target] = IF @ > revision THEN @ ELSE revision]
                /\ editable' = IF Fault = "permission"
                               THEN [editable EXCEPT ![target] = TRUE] ELSE editable
           ELSE UNCHANGED <<visible, high, editable>>
     /\ UNCHANGED <<selected, permission, remote>>
Revoke(t) ==
  /\ permission[t]
  /\ permission' = [permission EXCEPT ![t] = FALSE]
  /\ editable' = [editable EXCEPT ![t] = FALSE]
  /\ UNCHANGED <<selected, visible, high, remote, requests>>
StaleRead(t) ==
  /\ visible[t].revision > 0
  /\ visible' = IF Fault = "regress"
                THEN [visible EXCEPT ![t].revision = 0] ELSE visible
  /\ editable' = [editable EXCEPT ![t] = FALSE]
  /\ UNCHANGED <<selected, high, permission, remote, requests>>
Next == (\E t \in Targets : Select(t) \/ Revoke(t) \/ StaleRead(t))
     \/ (\E t \in Targets, i \in Operations : Start(t, i))
     \/ (\E i \in Operations : Write(i) \/ LoseObservation(i) \/ Receive(i))
Spec == Init /\ [][Next]_vars
TargetOnly == \A t \in Targets : visible[t].origin = t
NoRegression == \A t \in Targets : visible[t].revision >= high[t]
NoUnknownResend == \A t \in Targets : Cardinality(Unresolved(t)) <= 1
NoPermissionResurrection == \A t \in Targets : editable[t] => permission[t]
=============================================================================
