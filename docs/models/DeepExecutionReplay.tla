----------------------- MODULE DeepExecutionReplay -----------------------
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS NoLock, RepeatAttempt, SkipSavedResult

(* Focused #152/#156 Safety obligation: two callers of one frozen execution.
   Claim/inspect, durable attempt, external model effect and saved result are
   separate cut points. Failure after the effect can be known or unconfirmed;
   neither authorizes automatic replay. Object recreation after a clean
   terminal preserves attempt/result; mid-run crash recovery is outside scope.
   "saved" is a runtime output package, not validated business PASS/NG.
   No fairness: a guest may never complete. New accepted executions, input
   identity validation, filesystem durability and cleanup need separate tests. *)
Calls == {"a", "b"}
VARIABLES phase, owner, attempted, saved, effects
vars == <<phase, owner, attempted, saved, effects>>

Init == /\ phase = [c \in Calls |-> "idle"] /\ owner = "none"
        /\ attempted = FALSE /\ saved = FALSE /\ effects = 0
Invoke(c) == /\ phase[c] = "idle"
             /\ phase' = [phase EXCEPT ![c] = "waiting"]
             /\ UNCHANGED <<owner, attempted, saved, effects>>
Claim(c) == /\ phase[c] = "waiting" /\ (owner = "none" \/ NoLock)
            /\ owner' = c /\ phase' = [phase EXCEPT ![c] = "inspecting"]
            /\ UNCHANGED <<attempted, saved, effects>>
ReadFresh(c) == /\ phase[c] = "inspecting" /\ ~saved
                /\ (~attempted \/ RepeatAttempt)
                /\ phase' = [phase EXCEPT ![c] = "fresh"]
                /\ UNCHANGED <<owner, attempted, saved, effects>>
ReadSaved(c) == /\ phase[c] = "inspecting" /\ saved /\ ~SkipSavedResult
                /\ phase' = [phase EXCEPT ![c] = "done"]
                /\ owner' = IF owner = c THEN "none" ELSE owner
                /\ UNCHANGED <<attempted, saved, effects>>
Reject(c) == /\ phase[c] = "inspecting" /\ attempted /\ ~RepeatAttempt
             /\ (~saved \/ SkipSavedResult)
             /\ phase' = [phase EXCEPT ![c] = "rejected"]
             /\ owner' = IF owner = c THEN "none" ELSE owner
             /\ UNCHANGED <<attempted, saved, effects>>
MarkAttempt(c) == /\ phase[c] = "fresh" /\ attempted' = TRUE
                  /\ phase' = [phase EXCEPT ![c] = "prepared"]
                  /\ UNCHANGED <<owner, saved, effects>>
StartModel(c) == /\ phase[c] = "prepared" /\ effects' = effects + 1
                 /\ phase' = [phase EXCEPT ![c] = "running"]
                 /\ UNCHANGED <<owner, attempted, saved>>
Complete(c) == /\ phase[c] = "running" /\ saved' = TRUE
               /\ phase' = [phase EXCEPT ![c] = "done"]
               /\ owner' = IF owner = c THEN "none" ELSE owner
               /\ UNCHANGED <<attempted, effects>>
Fail(c) == /\ phase[c] = "running"
           /\ phase' = [phase EXCEPT ![c] = "failed"]
           /\ owner' = IF owner = c THEN "none" ELSE owner
           /\ UNCHANGED <<attempted, saved, effects>>
Next == \E c \in Calls : Invoke(c) \/ Claim(c) \/ ReadFresh(c) \/ ReadSaved(c)
                         \/ Reject(c) \/ MarkAttempt(c) \/ StartModel(c)
                         \/ Complete(c) \/ Fail(c)
Spec == Init /\ [][Next]_vars
TypeOK == /\ phase \in [Calls -> {"idle", "waiting", "inspecting", "fresh",
                                  "prepared", "running", "done", "failed", "rejected"}]
          /\ owner \in Calls \cup {"none"}
          /\ attempted \in BOOLEAN /\ saved \in BOOLEAN /\ effects \in 0..2
OneModelEffect == effects <= 1
SavedResultNotRejected == \A c \in Calls : phase[c] = "rejected" => ~saved

(* Focused #221 explicit-recovery obligation, not a refinement of Spec above.
   The earlier Spec remains the one-technical-attempt replay contract. This
   finite instance starts with known attempt 1 and permits explicit attempts
   2 and 3. Preparing, durably marking unknown, sending, and observing usage
   are distinct cuts. The existing attempted/saved variables here represent
   consumed technical identities / confirmed usage identities, respectively.
   The current attempt fence must name the latest known source. Merely finding
   an older valid candidate is insufficient. RepeatAttempt is the deliberate
   mutant that bypasses that fence, including changing a failed attempt's mode.
   Two sends suffice for the forbidden replay. No fairness or progress claim;
   storage integrity, budgets, source validation, and OS isolation are tested
   separately. ContainerRuntime's slot is assumed to have one process owner. *)
RecoveryAttempts == 1..3
RecoveryLatest == CHOOSE a \in attempted : \A b \in attempted : a >= b
RecoveryEligible == owner.source = RecoveryLatest /\ owner.source \in saved
                    /\ owner.attempt > RecoveryLatest
RecoveryInit == /\ phase = [a \in RecoveryAttempts |-> IF a = 1 THEN "known" ELSE "idle"]
                /\ owner = [attempt |-> 0, source |-> 0] /\ attempted = {1} /\ saved = {1} /\ effects = <<>>
RecoveryInspect(a, source) == /\ a \in 2..3 /\ phase[a] = "idle" /\ owner.attempt = 0
                              /\ source \in saved /\ source < a
                              /\ owner' = [attempt |-> a, source |-> source]
                              /\ phase' = [phase EXCEPT ![a] = "checking"]
                              /\ UNCHANGED <<attempted, saved, effects>>
RecoveryChangeMode(a) == /\ RepeatAttempt /\ phase[a] = "unknown" /\ owner.attempt = 0
                         /\ owner' = [attempt |-> a, source |-> 1]
                         /\ phase' = [phase EXCEPT ![a] = "checking"]
                         /\ UNCHANGED <<attempted, saved, effects>>
RecoveryPrepare == /\ owner.attempt # 0 /\ phase[owner.attempt] = "checking"
                   /\ (RecoveryEligible \/ RepeatAttempt)
                   /\ phase' = [phase EXCEPT ![owner.attempt] = IF RecoveryEligible THEN "prepared" ELSE "prepared_stale"]
                   /\ UNCHANGED <<owner, attempted, saved, effects>>
RecoveryReject == /\ owner.attempt # 0 /\ phase[owner.attempt] = "checking"
                  /\ ~RecoveryEligible /\ ~RepeatAttempt
                  /\ phase' = [phase EXCEPT ![owner.attempt] = "rejected"]
                  /\ owner' = [attempt |-> 0, source |-> 0] /\ UNCHANGED <<attempted, saved, effects>>
RecoveryMark == /\ owner.attempt # 0 /\ phase[owner.attempt] \in {"prepared", "prepared_stale"}
                /\ (owner.attempt \notin attempted \/ RepeatAttempt)
                /\ attempted' = attempted \cup {owner.attempt}
                /\ phase' = [phase EXCEPT ![owner.attempt] = IF @ = "prepared" THEN "ready" ELSE "ready_stale"]
                /\ UNCHANGED <<owner, saved, effects>>
RecoverySend == /\ owner.attempt # 0 /\ phase[owner.attempt] \in {"ready", "ready_stale"}
                /\ Len(effects) < 2
                /\ effects' = Append(effects, [attempt |-> owner.attempt, eligible |-> phase[owner.attempt] = "ready"])
                /\ phase' = [phase EXCEPT ![owner.attempt] = "running"]
                /\ UNCHANGED <<owner, attempted, saved>>
RecoveryKnown == /\ owner.attempt # 0 /\ phase[owner.attempt] = "running"
                 /\ saved' = saved \cup {owner.attempt}
                 /\ phase' = [phase EXCEPT ![owner.attempt] = "known"]
                 /\ owner' = [attempt |-> 0, source |-> 0] /\ UNCHANGED <<attempted, effects>>
RecoveryUnknown == /\ owner.attempt # 0 /\ phase[owner.attempt] \in {"ready", "ready_stale", "running"}
                   /\ phase' = [phase EXCEPT ![owner.attempt] = "unknown"]
                   /\ owner' = [attempt |-> 0, source |-> 0] /\ UNCHANGED <<attempted, saved, effects>>
RecoveryNext == (\E a \in 2..3, source \in 1..2 : RecoveryInspect(a, source))
                \/ (\E a \in 2..3 : RecoveryChangeMode(a))
                \/ RecoveryPrepare \/ RecoveryReject \/ RecoveryMark \/ RecoverySend \/ RecoveryKnown \/ RecoveryUnknown
RecoverySpec == RecoveryInit /\ [][RecoveryNext]_vars
RecoveryTypeOK == /\ phase \in [RecoveryAttempts -> {"idle", "known", "unknown", "checking", "prepared", "prepared_stale", "ready", "ready_stale", "running", "rejected"}]
                  /\ attempted \subseteq RecoveryAttempts /\ saved \subseteq attempted
                  /\ owner \in [attempt : 0..3, source : 0..2]
                  /\ Len(effects) <= 2
LatestSourceOwnsReplay == \A i \in 1..Len(effects) : effects[i].eligible
OneEffectPerTechnicalAttempt == Cardinality({effects[i].attempt : i \in 1..Len(effects)}) = Len(effects)
=============================================================================
