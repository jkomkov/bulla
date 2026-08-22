/-
  Certified semantic refinement.

  The statements here isolate the mathematical invariant used by the
  observability implementation.  They do not claim an end-to-end verified
  parser or extractor: Python differential tests remain the abstraction bridge.
-/

universe u v w

namespace InterpolantEnvelope

variable {World : Type u} {Observable : Type v} {Point : Type w}

/-- A selected observable family determines q when observationally identical
    admissible worlds cannot disagree about q. -/
def Determines
    (admissible : World → Prop)
    (q : World → Prop)
    (selected : Observable → Prop)
    (observe : Observable → World → Prop) : Prop :=
  ∀ left right, admissible left → admissible right →
    (∀ observable, selected observable →
      (observe observable left ↔ observe observable right)) →
    (q left ↔ q right)

/-- Every target-disagreeing pair is separated by at least one selected
    observable.  This is the finite planner's covering obligation. -/
def SeparatesOpposingPairs
    (admissible : World → Prop)
    (q : World → Prop)
    (selected : Observable → Prop)
    (observe : Observable → World → Prop) : Prop :=
  ∀ left right, admissible left → admissible right →
    ¬ (q left ↔ q right) →
    ∃ observable, selected observable ∧
      ¬ (observe observable left ↔ observe observable right)

/-- Observable separation is not a heuristic: it is exactly determination of
    the target over the declared admissible state. -/
theorem determines_iff_separates_opposing_pairs
    (admissible : World → Prop)
    (q : World → Prop)
    (selected : Observable → Prop)
    (observe : Observable → World → Prop) :
    Determines admissible q selected observe ↔
      SeparatesOpposingPairs admissible q selected observe := by
  classical
  constructor
  · intro determines left right leftAdmissible rightAdmissible targetDiffers
    apply Classical.byContradiction
    intro noSeparator
    have observationsAgree :
        ∀ observable, selected observable →
          (observe observable left ↔ observe observable right) := by
      intro observable observableSelected
      apply Classical.byContradiction
      intro observationDiffers
      exact noSeparator ⟨observable, observableSelected, observationDiffers⟩
    exact targetDiffers
      (determines left right leftAdmissible rightAdmissible observationsAgree)
  · intro separates left right leftAdmissible rightAdmissible observationsAgree
    apply Classical.byContradiction
    intro targetDiffers
    obtain ⟨observable, observableSelected, observationDiffers⟩ :=
      separates left right leftAdmissible rightAdmissible targetDiffers
    exact observationDiffers (observationsAgree observable observableSelected)

/-- Worlds on which every admissible interpretation accepts a point. -/
def DefinitelyPositive
    (admissible : World → Prop)
    (q : World → Point → Prop)
    (point : Point) : Prop :=
  ∀ world, admissible world → q world point

/-- Worlds on which every admissible interpretation rejects a point. -/
def DefinitelyNegative
    (admissible : World → Prop)
    (q : World → Point → Prop)
    (point : Point) : Prop :=
  ∀ world, admissible world → ¬ q world point

/-- The protocol routes points that are neither definitely positive nor
    definitely negative. -/
def Ambiguous
    (admissible : World → Prop)
    (q : World → Point → Prop)
    (point : Point) : Prop :=
  ¬ DefinitelyPositive admissible q point ∧
  ¬ DefinitelyNegative admissible q point

theorem definitelyPositive_monotone
    {prior next : World → Prop}
    (nextSubset : ∀ world, next world → prior world)
    (q : World → Point → Prop)
    {point : Point}
    (priorPositive : DefinitelyPositive prior q point) :
    DefinitelyPositive next q point := by
  intro world worldNext
  exact priorPositive world (nextSubset world worldNext)

theorem definitelyNegative_monotone
    {prior next : World → Prop}
    (nextSubset : ∀ world, next world → prior world)
    (q : World → Point → Prop)
    {point : Point}
    (priorNegative : DefinitelyNegative prior q point) :
    DefinitelyNegative next q point := by
  intro world worldNext
  exact priorNegative world (nextSubset world worldNext)

theorem ambiguity_antitone
    {prior next : World → Prop}
    (nextSubset : ∀ world, next world → prior world)
    (q : World → Point → Prop)
    {point : Point}
    (nextAmbiguous : Ambiguous next q point) :
    Ambiguous prior q point := by
  constructor
  · intro priorPositive
    exact nextAmbiguous.1
      (definitelyPositive_monotone nextSubset q priorPositive)
  · intro priorNegative
    exact nextAmbiguous.2
      (definitelyNegative_monotone nextSubset q priorNegative)

/-- The three envelope movements certified by one state refinement. -/
theorem envelope_refinement_sound
    {prior next : World → Prop}
    (nextSubset : ∀ world, next world → prior world)
    (q : World → Point → Prop) :
    (∀ point, DefinitelyPositive prior q point →
      DefinitelyPositive next q point) ∧
    (∀ point, DefinitelyNegative prior q point →
      DefinitelyNegative next q point) ∧
    (∀ point, Ambiguous next q point → Ambiguous prior q point) := by
  constructor
  · intro point positive
    exact definitelyPositive_monotone nextSubset q positive
  · constructor
    · intro point negative
      exact definitelyNegative_monotone nextSubset q negative
    · intro point ambiguous
      exact ambiguity_antitone nextSubset q ambiguous

/-- Authority epochs separate refinements from revisions at the type level. -/
structure SemanticState (World : Type u) where
  epoch : Nat
  admissible : World → Prop

def Refines (prior next : SemanticState World) : Prop :=
  prior.epoch = next.epoch ∧
  ∀ world, next.admissible world → prior.admissible world

def Preserves (prior next : SemanticState World) : Prop :=
  prior.epoch = next.epoch ∧
  ∀ world, prior.admissible world ↔ next.admissible world

def Revises (prior next : SemanticState World) : Prop :=
  prior.epoch ≠ next.epoch

def admitConstraint
    (state : SemanticState World)
    (constraint : World → Prop) : SemanticState World :=
  { epoch := state.epoch
    admissible := fun world => state.admissible world ∧ constraint world }

theorem admitted_constraint_refines
    (state : SemanticState World)
    (constraint : World → Prop) :
    Refines state (admitConstraint state constraint) := by
  constructor
  · rfl
  · intro world admitted
    exact admitted.1

theorem preserve_is_refinement
    {prior next : SemanticState World}
    (preserves : Preserves prior next) :
    Refines prior next := by
  constructor
  · exact preserves.1
  · intro world nextAdmissible
    exact (preserves.2 world).mpr nextAdmissible

theorem refinement_never_changes_epoch
    {prior next : SemanticState World}
    (refines : Refines prior next) :
    ¬ Revises prior next := by
  intro revises
  exact revises refines.1

end InterpolantEnvelope
