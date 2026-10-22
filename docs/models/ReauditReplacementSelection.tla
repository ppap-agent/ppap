------------------- MODULE ReauditReplacementSelection -------------------
EXTENDS FiniteSets
\* #163 D9 / #165 focused input-selection Safety obligation, not refinement.
\* Two frozen original versions distinguish reassignment; one uploaded version
\* distinguishes user intent from automatic exclusion. No IDs are reused.
\* Each action is one synchronous React state update. Network/upload epochs,
\* validation, rules, acceptance, fees and historical results are out of scope.
\* No fairness, Liveness, persistence or full-system correctness claim.
\* Legacy inputs whose prior choice was lost are outside this model; the UI
\* readback test checks their conservative unselected fallback separately.
CONSTANT Fault
Originals == {"A", "B"}
Files == Originals \cup {"C"}
Choices == {"selected", "unselected", "removed"}
VARIABLES intent, visible, replacement
vars == <<intent, visible, replacement>>

Covered(i, r, f) == f \in Originals /\ i["C"] = "selected" /\ r = f
Expected(i, r) == [f \in Files |->
  IF Covered(i, r, f) /\ i[f] = "selected" THEN "replaced" ELSE i[f]]
\* "replaced" projects to selection=removed plus the existing automatic reason.
Reconcile(v, r) == [f \in Files |->
  IF f \in Originals /\ v["C"] = "selected" /\ r = f
  THEN IF v[f] = "selected" THEN "replaced" ELSE v[f]
  ELSE IF v[f] = "replaced" THEN "selected" ELSE v[f]]

Init == /\ intent = [f \in Files |-> "selected"]
        /\ visible = intent /\ replacement = "none"
Choose(r) == /\ visible["C"] = "selected"
             /\ replacement' = r
             /\ visible' = IF Fault = "retain"
                           THEN [f \in Files |->
                             IF f = r /\ visible[f] = "selected"
                             THEN "replaced" ELSE visible[f]]
                           ELSE Reconcile(visible, r)
             /\ UNCHANGED intent
Select(f, s) == /\ visible[f] \in {"selected", "unselected"}
                /\ intent' = [intent EXCEPT ![f] = s]
                /\ visible' = IF Fault = "selection"
                              THEN [visible EXCEPT ![f] = s]
                              ELSE Reconcile([visible EXCEPT ![f] = s], replacement)
                /\ UNCHANGED replacement
SelectAll(s) ==
  LET eligible == {f \in Files : visible[f] # "removed"}
      nextIntent == [f \in Files |-> IF f \in eligible THEN s ELSE intent[f]]
      nextVisible == [f \in Files |->
        IF f \in eligible /\ (Fault # "bulk" \/ visible[f] # "replaced")
        THEN s ELSE visible[f]]
  IN /\ intent' = nextIntent
     /\ visible' = Reconcile(nextVisible, replacement)
     /\ UNCHANGED replacement
Next == (\E r \in Originals \cup {"none"}: Choose(r))
        \/ (\E f \in Files, s \in Choices: Select(f, s))
        \/ (\E s \in {"selected", "unselected"}: SelectAll(s))
Spec == Init /\ [][Next]_vars
TypeOK == /\ intent \in [Files -> Choices]
          /\ visible \in [Files -> Choices \cup {"replaced"}]
          /\ replacement \in Originals \cup {"none"}
PreservesUserIntent == visible = Expected(intent, replacement)
=============================================================================
