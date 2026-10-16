------------------------ MODULE ProductAccessBootstrap ------------------------
EXTENDS Naturals

\* #165 focused Safety obligation for one anonymous /me probe followed by host
\* login. The probe's 401 is not revocation of an authenticated session. A later
\* authenticated 401 must hide the product. Provider response and UI commit are
\* separate; the provider may never respond (stuttering). No fairness/liveness
\* claim, constraints, symmetry or overrides. Deadlock checking is off for open
\* environment quiescence. This is not full-system refinement: OAuth state/ACLs,
\* cookies, cross-tab revocation, retries/unmounts and external SSO are excluded.

CONSTANT Fault
ASSUME Fault \in {"none", "anonymous_401", "ignore_expiry"}

VARIABLES phase, epoch, principal, expired
vars == <<phase, epoch, principal, expired>>

Init == /\ phase = "probe" /\ epoch = 0
        /\ principal = FALSE /\ expired = FALSE

AnonymousProbe ==
    /\ phase = "probe"
    /\ phase' = "host_pending"
    /\ epoch' = IF Fault = "anonymous_401" THEN epoch + 1 ELSE epoch
    /\ UNCHANGED <<principal, expired>>

HostResponse ==
    /\ phase = "host_pending"
    /\ phase' = "host_returned"
    /\ UNCHANGED <<epoch, principal, expired>>

CommitHost ==
    /\ phase = "host_returned"
    /\ phase' = "settled"
    /\ principal' = (epoch = 0)
    /\ UNCHANGED <<epoch, expired>>

AuthenticatedExpiry ==
    /\ principal /\ ~expired
    /\ expired' = TRUE /\ epoch' = epoch + 1
    /\ principal' = IF Fault = "ignore_expiry" THEN principal ELSE FALSE
    /\ UNCHANGED phase

Next == AnonymousProbe \/ HostResponse \/ CommitHost \/ AuthenticatedExpiry
Spec == Init /\ [][Next]_vars

TypeOK == /\ phase \in {"probe", "host_pending", "host_returned", "settled"}
          /\ epoch \in 0..2 /\ principal \in BOOLEAN /\ expired \in BOOLEAN
HostSuccessVisible == phase = "settled" /\ ~expired => principal
ExpiredSessionHidden == expired => ~principal
NoPrematureAccess == phase # "settled" => ~principal
=============================================================================
