--------------------- MODULE GreenfieldSourceIdentity ---------------------
EXTENDS Naturals, TLC
CONSTANT Unsafe
VARIABLES selected, pending, shown, stored, switched, lost
vars == <<selected, pending, shown, stored, switched, lost>>
Files == {1, 2}
Content == [f \in Files |-> f]
Init == /\ selected = 1 /\ pending = 0 /\ shown = 0
        /\ stored = Content /\ switched = FALSE /\ lost = FALSE
Read == /\ pending = 0 /\ stored[selected] # 0
        /\ pending' = selected /\ UNCHANGED <<selected, shown, stored, switched, lost>>
Switch == /\ ~switched /\ selected' = 2 /\ switched' = TRUE /\ shown' = 0
          /\ UNCHANGED <<pending, stored, lost>>
Complete == /\ pending # 0
            /\ shown' = IF Unsafe \/ pending = selected THEN pending ELSE shown
            /\ pending' = 0 /\ UNCHANGED <<selected, stored, switched, lost>>
Lose == /\ ~lost /\ stored' = [stored EXCEPT ![1] = 0] /\ lost' = TRUE
        /\ UNCHANGED <<selected, pending, shown, switched>>
Restore(c) == /\ stored[1] = 0 /\ (Unsafe \/ c = Content[1])
              /\ stored' = [stored EXCEPT ![1] = c]
              /\ UNCHANGED <<selected, pending, shown, switched, lost>>
Next == Read \/ Switch \/ Complete \/ Lose \/ (\E c \in Files: Restore(c))
Spec == Init /\ [][Next]_vars
TypeOK == /\ selected \in Files /\ pending \in Files \cup {0} /\ shown \in Files \cup {0}
          /\ stored \in [Files -> Files \cup {0}] /\ switched \in BOOLEAN /\ lost \in BOOLEAN
SelectedSourceOnly == shown = 0 \/ shown = selected
ImmutableSource == \A f \in Files: stored[f] = 0 \/ stored[f] = Content[f]
=============================================================================
