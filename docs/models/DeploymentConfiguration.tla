-------------------- MODULE DeploymentConfiguration --------------------
EXTENDS Naturals
CONSTANT SkipRollout, SkipReadback
(* Focused release Safety obligation: three process instances, one configuration
   replacement. ConfigMap publication and each Pod replacement are distinct
   external effects. Observe represents a fresh, successful read of all process
   configurations; failed/no response observations cannot verify a release.
   No fairness or Kubernetes convergence/liveness claim. Source CI, registry,
   secrets, DB durability and model quality remain controlled/live evidence. *)
Processes == {"api", "worker", "controller"}
VARIABLES desired, template, running, verified
vars == <<desired, template, running, verified>>
Init == /\ desired = "a"
        /\ template = [p \in Processes |-> "a"]
        /\ running = template
        /\ verified = FALSE
Publish == /\ desired = "a" /\ desired' = "b" /\ verified' = FALSE
           /\ UNCHANGED <<template, running>>
Apply(p) == /\ template[p] # desired
            /\ template' = IF SkipRollout THEN template ELSE [template EXCEPT ![p] = desired]
            /\ UNCHANGED <<desired, running, verified>>
Replace(p) == /\ running[p] # template[p]
              /\ running' = [running EXCEPT ![p] = template[p]]
              /\ UNCHANGED <<desired, template, verified>>
Observe == /\ (\A p \in Processes : running[p] = desired) \/ SkipReadback
           /\ verified' = TRUE
           /\ UNCHANGED <<desired, template, running>>
Next == Publish \/ Observe \/ (\E p \in Processes : Apply(p) \/ Replace(p))
Spec == Init /\ [][Next]_vars
LoadedConfiguration == verified => (\A p \in Processes : running[p] = desired)
=============================================================================
