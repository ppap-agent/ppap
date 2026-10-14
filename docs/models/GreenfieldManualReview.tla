--------------------- MODULE GreenfieldManualReview ---------------------
EXTENDS Naturals

\* Focused obligation: one applicable clause, two document/requirement
\* versions. Human evidence does not rewrite the machine observation.
\* Completeness of the excerpt's meaning is a human responsibility, not
\* proved here. No identity provider, storage failures or liveness claim.
CONSTANTS FaultyEvidence, FaultyFreshness
VARIABLES doc, req, machine, human, citationDoc, citationReq, reason, decision
vars == <<doc, req, machine, human, citationDoc, citationReq, reason, decision>>

Init == /\ doc = 1 /\ req = 1
        /\ machine \in {"unknown", "failed"}
        /\ human = "pending" /\ citationDoc = 0 /\ citationReq = 0
        /\ reason = FALSE
        /\ decision = [doc |-> 0, req |-> 0, sourceDoc |-> 0, sourceReq |-> 0,
                       explained |-> FALSE, human |-> "pending", machine |-> machine]

Judge(v) == /\ decision.doc = 0 /\ v \in {"satisfied", "unsatisfied", "unknown"}
            /\ human' = v
            /\ UNCHANGED <<doc, req, machine, citationDoc, citationReq, reason, decision>>
Cite == /\ decision.doc = 0
        /\ citationDoc' = doc /\ citationReq' = req
        /\ UNCHANGED <<doc, req, machine, human, reason, decision>>
RemoveCitation == /\ decision.doc = 0 /\ citationDoc' = 0 /\ citationReq' = 0
                  /\ UNCHANGED <<doc, req, machine, human, reason, decision>>
Explain == /\ decision.doc = 0 /\ reason' = ~reason
           /\ UNCHANGED <<doc, req, machine, human, citationDoc, citationReq, decision>>
ChangeDocument == /\ doc = 1 /\ doc' = 2
                  /\ UNCHANGED <<req, machine, human, citationDoc, citationReq, reason, decision>>
ChangeRequirement == /\ req = 1 /\ req' = 2
                     /\ UNCHANGED <<doc, machine, human, citationDoc, citationReq, reason, decision>>

Approve == /\ decision.doc = 0 /\ human = "satisfied"
           /\ (FaultyEvidence \/ (reason /\ citationDoc > 0 /\ citationReq > 0))
           /\ (FaultyFreshness \/ (citationDoc = doc /\ citationReq = req))
           /\ decision' = [doc |-> doc, req |-> req, sourceDoc |-> citationDoc,
                           sourceReq |-> citationReq, explained |-> reason,
                           human |-> human, machine |-> machine]
           /\ UNCHANGED <<doc, req, machine, human, citationDoc, citationReq, reason>>

Next == (\E v \in {"satisfied", "unsatisfied", "unknown"}: Judge(v))
        \/ Cite \/ RemoveCitation \/ Explain \/ ChangeDocument
        \/ ChangeRequirement \/ Approve
Spec == Init /\ [][Next]_vars
MachineRemainsUnknown == machine \in {"unknown", "failed"}
EvidenceRequired == decision.doc > 0 =>
  decision.explained /\ decision.sourceDoc > 0 /\ decision.sourceReq > 0
EvidenceFreshAtDecision == decision.doc > 0 =>
  decision.sourceDoc = decision.doc /\ decision.sourceReq = decision.req
HumanSatisfied == decision.doc > 0 => decision.human = "satisfied"
=============================================================================
