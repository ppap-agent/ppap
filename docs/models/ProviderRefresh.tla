-------------------------- MODULE ProviderRefresh --------------------------
EXTENDS Naturals
CONSTANTS DuplicateClaim, RetryUnknown, WrongOwner
VARIABLES links, state, generation, calls, pending, committedToOwner
vars == <<links, state, generation, calls, pending, committedToOwner>>
Owners == {0, 1}
Consumers == {"browser", "clientA", "clientB"}

\* #206 focused Safety obligation. Two authorization lifetimes, two token
\* generations and three consumers distinguish a shared refresh from a new
\* login. Claim commits before the provider call. Lost replies/crashes retain
\* the claim. Timeout cannot authorize replay. Re-login creates a different
\* owner; existing client grants never silently inherit it. Revocation and
\* object ACLs remain in McpDelegation and implementation tests.
Init == /\ links = [c \in Consumers |-> 0]
        /\ state = [a \in Owners |-> "ready"]
        /\ generation = [a \in Owners |-> 0]
        /\ calls = [a \in Owners |-> 0]
        /\ pending = {}
        /\ committedToOwner = TRUE
Claim(c) == LET a == links[c] IN
            /\ generation[a] = 0
            /\ (state[a] = "ready" \/ (DuplicateClaim /\ state[a] = "refreshing")
                \/ (RetryUnknown /\ state[a] = "unknown"))
            /\ calls[a] < 2
            /\ state' = [state EXCEPT ![a] = "refreshing"]
            /\ calls' = [calls EXCEPT ![a] = @ + 1]
            /\ pending' = pending \cup {a}
            /\ UNCHANGED <<links, generation, committedToOwner>>
Lose(a) == /\ a \in pending /\ state[a] = "refreshing"
           /\ state' = [state EXCEPT ![a] = "unknown"]
           /\ UNCHANGED <<links, generation, calls, pending, committedToOwner>>
Complete(a) == LET target == IF WrongOwner THEN links["browser"] ELSE a IN
               /\ a \in pending
               /\ state[a] = "refreshing"
               /\ state' = [state EXCEPT ![target] = "ready"]
               /\ generation' = [generation EXCEPT ![target] = 1]
               /\ pending' = pending \ {a}
               /\ committedToOwner' = (committedToOwner /\ target = a)
               /\ UNCHANGED <<links, calls>>
Login == /\ links["browser"] = 0
         /\ links' = [links EXCEPT !["browser"] = 1]
         /\ UNCHANGED <<state, generation, calls, pending, committedToOwner>>
Next == (\E c \in Consumers: Claim(c)) \/ (\E a \in Owners: Lose(a) \/ Complete(a)) \/ Login
Spec == Init /\ [][Next]_vars
SingleRefresh == \A a \in Owners: calls[a] <= 1
CorrectOwner == committedToOwner
=============================================================================
