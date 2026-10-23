-------------------------- MODULE McpEffectBinding --------------------------
EXTENDS Naturals
CONSTANTS SkipBoundaryCheck, ReadLiveAtSend
VARIABLES source, phase, captured, sent
vars == <<source, phase, captured, sent>>

\* #206 focused Safety obligation, not a refinement or liveness proof.
\* One approved call and two source revisions distinguish an approved payload
\* (0) from a later Web edit (1); 2 means no captured/sent payload.
\* Gateway check, native capture, external send
\* and reply are separate boundaries. An edit is allowed at every boundary.
\* Native capture verifies the same materialized value that the effect uses;
\* it does not lock the user out of future edits. Provider processing is
\* abstracted as one accepted payload. Partial multi-call effects, ACLs, token
\* revocation and per-request at-most-once ownership remain outside this model.
\* A crash or lost reply retains uncertainty; there is no automatic rearm.
\* No fairness, time bounds, symmetry or state constraints are assumed.
Init == /\ source = 0 /\ phase = "approved"
        /\ captured = 2 /\ sent = 2
Edit == /\ source = 0 /\ source' = 1
        /\ UNCHANGED <<phase, captured, sent>>
Claim == /\ phase = "approved"
         /\ phase' = IF source = 0 THEN "claimed" ELSE "refused"
         /\ UNCHANGED <<source, captured, sent>>
Capture == /\ phase = "claimed"
           /\ IF SkipBoundaryCheck \/ source = 0
                 THEN /\ phase' = "captured" /\ captured' = source
                 ELSE /\ phase' = "refused" /\ UNCHANGED captured
           /\ UNCHANGED <<source, sent>>
Send == /\ phase = "captured"
        /\ sent' = IF ReadLiveAtSend THEN source ELSE captured
        /\ phase' = "sent"
        /\ UNCHANGED <<source, captured>>
Reply == /\ phase = "sent" /\ phase' = "done"
         /\ UNCHANGED <<source, captured, sent>>
Lose == /\ phase \in {"claimed", "captured", "sent"}
        /\ phase' = "unknown"
        /\ UNCHANGED <<source, captured, sent>>
Next == Edit \/ Claim \/ Capture \/ Send \/ Reply \/ Lose
Spec == Init /\ [][Next]_vars
OnlyApprovedContent == sent \in {2, 0}
=============================================================================
