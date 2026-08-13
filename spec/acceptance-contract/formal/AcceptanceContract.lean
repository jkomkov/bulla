import Std

/-!
Finite abstraction for bulla.acceptance-contract/0.1-experimental.

The model isolates the canonical rollback-test requirement. It establishes the
decision ordering, conditional closure within the declared one-item catalog,
policy-revision distinction, and the separation of eligibility from later
authorization and execution. It does not prove parser correctness, worldly
truth, denominator completeness, or legal enforceability.
-/

namespace AcceptanceContract

inductive EvidenceState where
  | missing
  | pass
  | fail
  | ambiguous
  deriving DecidableEq

inductive Decision where
  | proceed
  | holdForEvidence
  | refuse
  | escalate
  deriving DecidableEq

inductive Eligibility where
  | eligible
  | ineligible
  | challengeRequired
  deriving DecidableEq

def evaluateEvidence : EvidenceState → Decision
  | .missing => .holdForEvidence
  | .pass => .proceed
  | .fail => .refuse
  | .ambiguous => .escalate

def consequence : Decision → Eligibility
  | .proceed => .eligible
  | .holdForEvidence => .ineligible
  | .refuse => .ineligible
  | .escalate => .challengeRequired

theorem claim_non_amplification :
    evaluateEvidence .missing = .holdForEvidence := by
  rfl

theorem negative_evidence_refuses :
    evaluateEvidence .fail = .refuse := by
  rfl

theorem evidence_removal_non_improvement :
    evaluateEvidence .pass = .proceed ∧
      evaluateEvidence .missing ≠ .proceed := by
  simp [evaluateEvidence]

inductive RequestId where
  | rollbackTest
  deriving DecidableEq

def closureWithinDeclaredCatalog : EvidenceState → List RequestId
  | .missing => [.rollbackTest]
  | _ => []

theorem conditional_closure_sound_in_catalog :
    closureWithinDeclaredCatalog .missing = [.rollbackTest] ∧
      evaluateEvidence .pass = .proceed := by
  simp [closureWithinDeclaredCatalog, evaluateEvidence]

structure PolicyIdentity where
  revision : Nat
  deriving DecidableEq

def revise (policy : PolicyIdentity) : PolicyIdentity :=
  { revision := policy.revision + 1 }

theorem policy_revision_non_equivalence (policy : PolicyIdentity) :
    revise policy ≠ policy := by
  intro equality
  have fieldEquality : (revise policy).revision = policy.revision :=
    congrArg PolicyIdentity.revision equality
  simp [revise] at fieldEquality

inductive Authorization where
  | notIssued
  | authorized
  deriving DecidableEq

inductive Execution where
  | notAttempted
  | attempted
  deriving DecidableEq

def canonicalAuthorization (_decision : Decision) : Authorization :=
  .notIssued

def canonicalExecution (_decision : Decision) : Execution :=
  .notAttempted

theorem eligibility_does_not_authorize_or_execute :
    consequence .proceed = .eligible ∧
      canonicalAuthorization .proceed = .notIssued ∧
      canonicalExecution .proceed = .notAttempted := by
  simp [consequence, canonicalAuthorization, canonicalExecution]

end AcceptanceContract
