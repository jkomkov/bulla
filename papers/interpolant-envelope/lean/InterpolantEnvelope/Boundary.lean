/-
  Semantic Boundary v0.3.

  These results name two limits used by the executable profile.  Logical
  consequence is model-relative and cannot manufacture a ground warrant; and
  a same-observation/different-target pair blocks any sound total decision over
  that observation language.  The trace result is the abstraction behind the
  standalone refinement certificate.
-/

import InterpolantEnvelope.Refinement

universe u v

namespace InterpolantEnvelope.Boundary

variable {World : Type u} {Observable : Type v}

/-- qE is proof evidence internal to a model; qW is a separate ground warrant. -/
structure ClaimChain where
  qWorld : Prop
  qEntailment : Prop
  qSettlement : Prop
  settlementRequiresEntailment : qSettlement → qEntailment

/-- An entailment, however strong, cannot discharge an independently absent
    ground warrant.  The theorem is intentionally modest: the separation is a
    type boundary, not a metaphysical claim about truth. -/
theorem entailment_does_not_manufacture_ground
    (chain : ClaimChain)
    (groundAbsent : ¬ chain.qWorld) :
    chain.qEntailment → ¬ chain.qWorld := by
  intro _
  exact groundAbsent

/-- The finite enrichment planner's opposing-pair obligation is exactly the
    model-relative grounding-determination criterion. -/
theorem grounding_determined_iff_all_opposing_pairs_separated
    (admissible : World → Prop)
    (q : World → Prop)
    (selected : Observable → Prop)
    (observe : Observable → World → Prop) :
    Determines admissible q selected observe ↔
      SeparatesOpposingPairs admissible q selected observe :=
  determines_iff_separates_opposing_pairs admissible q selected observe

/-- A mixed observation class (same selected observations, different q) rules
    out every sound total decision on that observation language. -/
theorem mixed_observation_class_blocks_total_decision
    (admissible : World → Prop)
    (q : World → Prop)
    (selected : Observable → Prop)
    (observe : Observable → World → Prop)
    {left right : World}
    (leftAdmissible : admissible left)
    (rightAdmissible : admissible right)
    (sameObservations : ∀ observable, selected observable →
      (observe observable left ↔ observe observable right))
    (differentTarget : ¬ (q left ↔ q right)) :
    ¬ Determines admissible q selected observe := by
  intro determines
  exact differentTarget
    (determines left right leftAdmissible rightAdmissible sameObservations)

inductive Decision where
  | rely
  | refuse
  | ambiguous
  deriving DecidableEq

/-- A trace refinement may resolve ambiguity, but it may not retract either
    already certified surface. -/
def TraceRefines (prior next : World → Decision) : Prop :=
  (∀ world, prior world = .rely → next world = .rely) ∧
  (∀ world, prior world = .refuse → next world = .refuse) ∧
  (∀ world, next world = .ambiguous → prior world = .ambiguous)

theorem trace_refinement_preserves_decided_surfaces
    {prior next : World → Decision}
    (refines : TraceRefines prior next) :
    (∀ world, prior world = .rely → next world = .rely) ∧
    (∀ world, prior world = .refuse → next world = .refuse) :=
  ⟨refines.1, refines.2.1⟩

theorem trace_refinement_trans
    {first middle last : World → Decision}
    (firstMiddle : TraceRefines first middle)
    (middleLast : TraceRefines middle last) :
    TraceRefines first last := by
  constructor
  · intro world firstRelies
    exact middleLast.1 world (firstMiddle.1 world firstRelies)
  · constructor
    · intro world firstRefuses
      exact middleLast.2.1 world (firstMiddle.2.1 world firstRefuses)
    · intro world lastAmbiguous
      exact firstMiddle.2.2 world (middleLast.2.2 world lastAmbiguous)

end InterpolantEnvelope.Boundary
