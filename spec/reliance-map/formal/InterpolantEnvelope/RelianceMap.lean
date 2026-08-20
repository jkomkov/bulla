/-
  Finite Reliance Map extension to Handoff Admission.

  This model proves the correction-classification guards mirrored by the wire
  fixtures. It does not prove graph parser correctness or substantive truth.
-/

import InterpolantEnvelope.HandoffAdmission

namespace InterpolantEnvelope.RelianceMap

open InterpolantEnvelope.HandoffAdmission

/-- The three correction-recall results remain distinct. -/
inductive RecallDisposition where
  | affected
  | notAffected
  | undetermined
  deriving DecidableEq

/-- Known reachability takes precedence. Without a known path, only declared
    complete ancestry permits a negative result. -/
def recallDisposition (knownPath ancestryComplete : Bool) : RecallDisposition :=
  if knownPath then .affected
  else if ancestryComplete then .notAffected
  else .undetermined

theorem known_path_is_affected (complete : Bool) :
    recallDisposition true complete = .affected := by
  simp [recallDisposition]

/-- The scale profile reuses Handoff Admission reachability: an actual
    declared path supplies the known-path premise and an explicit witness. -/
theorem declared_path_is_affected {Node : Type u} (edge : Node → Node → Prop)
    (corrected target : Node) (path : Reaches edge corrected target)
    (complete : Bool) :
    recallDisposition true complete = .affected ∧
    ∃ witness, Reaches edge corrected witness ∧ witness = target := by
  exact ⟨known_path_is_affected complete,
    correction_path_is_affected edge corrected target path⟩

theorem incomplete_ancestry_never_clears :
    recallDisposition false false = .undetermined := by
  simp [recallDisposition]

theorem complete_unrelated_branch_is_not_affected :
    recallDisposition false true = .notAffected := by
  simp [recallDisposition]

/-- Recall has no rollback or consequence-authority constructor. -/
structure RecallEffect where
  disposition : RecallDisposition
  recheckRequired : Bool
  rollbackAuthorized : Bool
  consequenceAuthorized : Bool

def recallEffect (knownPath ancestryComplete : Bool) : RecallEffect :=
  let disposition := recallDisposition knownPath ancestryComplete
  {
    disposition
    recheckRequired := disposition == .affected
    rollbackAuthorized := false
    consequenceAuthorized := false
  }

theorem recall_is_not_rollback (path complete : Bool) :
    (recallEffect path complete).rollbackAuthorized = false ∧
    (recallEffect path complete).consequenceAuthorized = false := by
  simp [recallEffect]

/-- A separately accepted digest cannot be silently reused for changed graph
    bytes when the digest values are known to differ. -/
theorem changed_graph_digest_is_not_accepted {Digest : Type u}
    [DecidableEq Digest] (accepted supplied : Digest) (changed : supplied ≠ accepted) :
    ¬ supplied = accepted := by
  exact changed

end InterpolantEnvelope.RelianceMap
