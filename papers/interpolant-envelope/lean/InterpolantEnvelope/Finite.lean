/-
  Finite semantic spine for the Interpolant Envelope.

  This file does not claim the SCPI Beth-for-sites perimeter. It proves the
  finite semantic obligations consumed by the FRSL-1 reference checker:

  1. an independently checked explicit definition defines the target;
  2. a same-reduct/different-target pair rules out a fixed-language definer;
  3. the positive and refusal surfaces of a partial envelope are sound; and
  4. passing the declared package gates preserves required refusals.

  The executable checker is related to these abstractions by Python tests; a
  verified compiler from JSON bytes to these Lean objects remains future work.
-/

universe u v w

namespace InterpolantEnvelope

variable {Model : Type u} {Feature : Type v} {Point : Type w}

/-- A protected reduct exposes exactly the fixed-language feature predicates. -/
abbrev Reduct (Feature : Type v) := Feature → Prop

/-- An explicit predicate may inspect only a protected reduct and a target point. -/
abbrev ExplicitPredicate (Feature : Type v) (Point : Type w) :=
  Reduct Feature → Point → Prop

/-- Semantic explicit definition over the declared admissible finite models. -/
def ExplicitlyDefines
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (predicate : ExplicitPredicate Feature Point) : Prop :=
  ∀ model point, admissible model →
    (predicate (shared model) point ↔ target model point)

/-- Result 1: the checked two-copy obligation is exactly an explicit definition. -/
theorem checked_interpolant_defines
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (interpolant : ExplicitPredicate Feature Point)
    (checked :
      ∀ model point, admissible model →
        (interpolant (shared model) point ↔ target model point)) :
    ExplicitlyDefines admissible shared target interpolant :=
  checked

/-- The executable two-copy check is naturally split into the two Craig
    directions: target truth entails the shared interpolant, while target
    falsity excludes it. FRSL-1 target evaluation is decidable, so these
    obligations reconstruct the explicit definition. -/
theorem two_copy_interpolant_defines
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (interpolant : ExplicitPredicate Feature Point)
    (targetDecidable : ∀ model point, Decidable (target model point))
    (trueImpliesInterpolant :
      ∀ model point, admissible model → target model point →
        interpolant (shared model) point)
    (falseExcludesInterpolant :
      ∀ model point, admissible model → ¬ target model point →
        ¬ interpolant (shared model) point) :
    ExplicitlyDefines admissible shared target interpolant := by
  intro model point modelAdmissible
  letI := targetDecidable model point
  constructor
  · intro interpolantHolds
    by_cases targetHolds : target model point
    · exact targetHolds
    · exact False.elim
        ((falseExcludesInterpolant model point modelAdmissible targetHolds)
          interpolantHolds)
  · exact trueImpliesInterpolant model point modelAdmissible

/-- Result 2: two admissible expansions of one reduct that disagree on the
    target rule out every explicit predicate in the fixed language. -/
theorem two_expansions_rule_out_explicit_definition
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    {left right : Model}
    {point : Point}
    (leftAdmissible : admissible left)
    (rightAdmissible : admissible right)
    (sameReduct : shared left = shared right)
    (differentTarget : target left point ≠ target right point) :
    ¬ ∃ predicate : ExplicitPredicate Feature Point,
        ExplicitlyDefines admissible shared target predicate := by
  rintro ⟨predicate, defines⟩
  apply differentTarget
  apply propext
  calc
    target left point ↔ predicate (shared left) point :=
      (defines left point leftAdmissible).symm
    _ ↔ predicate (shared right) point := by rw [sameReduct]
    _ ↔ target right point := defines right point rightAdmissible

/-- A three-way envelope carries only the surfaces it can justify. -/
structure Envelope
    (Feature : Type v)
    (Point : Type w) where
  relyWhen : ExplicitPredicate Feature Point
  refuseWhen : ExplicitPredicate Feature Point

/-- RELY must be a subset of target truth; REFUSE must be a subset of target
    falsity. The residual is intentionally left to ESCALATE. -/
def EnvelopeSound
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (envelope : Envelope Feature Point) : Prop :=
  (∀ model point, admissible model →
      envelope.relyWhen (shared model) point → target model point) ∧
  (∀ model point, admissible model →
      envelope.refuseWhen (shared model) point → ¬ target model point)

theorem rely_surface_sound
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (envelope : Envelope Feature Point)
    (sound : EnvelopeSound admissible shared target envelope) :
    ∀ model point, admissible model →
      envelope.relyWhen (shared model) point → target model point :=
  sound.1

theorem refusal_surface_sound
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (envelope : Envelope Feature Point)
    (sound : EnvelopeSound admissible shared target envelope) :
    ∀ model point, admissible model →
      envelope.refuseWhen (shared model) point → ¬ target model point :=
  sound.2

theorem sound_envelope_never_relies_and_refuses
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (envelope : Envelope Feature Point)
    (sound : EnvelopeSound admissible shared target envelope)
    {model : Model}
    {point : Point}
    (modelAdmissible : admissible model) :
    ¬ (envelope.relyWhen (shared model) point ∧
       envelope.refuseWhen (shared model) point) := by
  rintro ⟨relies, refuses⟩
  exact (sound.2 model point modelAdmissible refuses)
    (sound.1 model point modelAdmissible relies)

/-- The semantic gates carried by a full emitted package. The first two fields
    remain abstract here because gluing and conservativity have their own
    established scoped formalizations; this result does not universalize them. -/
structure FullPackageGates
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (predicate : ExplicitPredicate Feature Point) where
  gluing : Prop
  conservativity : Prop
  definability : ExplicitlyDefines admissible shared target predicate
  preservedRefusals :
    ∀ model point, admissible model → ¬ target model point →
      ¬ predicate (shared model) point

/-- Result 3: a full package passing all semantic gates defines every positive
    point and erases no required refusal. -/
theorem full_package_safe
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    (predicate : ExplicitPredicate Feature Point)
    (gates : FullPackageGates admissible shared target predicate) :
    (∀ model point, admissible model →
      (predicate (shared model) point ↔ target model point)) ∧
    (∀ model point, admissible model → ¬ target model point →
      ¬ predicate (shared model) point) :=
  ⟨gates.definability, gates.preservedRefusals⟩

/-- Inequivalent minima outside the admissible theory cannot be selected by
    semantic correctness alone. This proposition records the choice witness;
    assigning institutional authority is intentionally outside the theorem. -/
structure NonUniqueMinimum
    (first second : ExplicitPredicate Feature Point) where
  witnessReduct : Reduct Feature
  witnessPoint : Point
  inequivalent :
    first witnessReduct witnessPoint ≠ second witnessReduct witnessPoint

/-- A candidate's governance-relevant identity is protected behavior plus its
    declared finite cost, not parser syntax or a package hash. -/
structure ChoiceCandidate
    (Feature : Type v)
    (Point : Type w) where
  predicate : ExplicitPredicate Feature Point
  cost : Nat

/-- The executable choice quotient used by result v0.2. Two candidates occupy
    one class exactly when every protected observation agrees and cost agrees. -/
def SameChoiceClass
    (first second : ChoiceCandidate Feature Point) : Prop :=
  first.cost = second.cost ∧
  ∀ reduct point,
    (first.predicate reduct point ↔ second.predicate reduct point)

theorem sameChoiceClass_refl
    (candidate : ChoiceCandidate Feature Point) :
    SameChoiceClass candidate candidate := by
  constructor
  · rfl
  · intro reduct point
    rfl

theorem sameChoiceClass_symm
    {first second : ChoiceCandidate Feature Point}
    (same : SameChoiceClass first second) :
    SameChoiceClass second first := by
  constructor
  · exact same.1.symm
  · intro reduct point
    exact (same.2 reduct point).symm

theorem sameChoiceClass_trans
    {first second third : ChoiceCandidate Feature Point}
    (firstSecond : SameChoiceClass first second)
    (secondThird : SameChoiceClass second third) :
    SameChoiceClass first third := by
  constructor
  · exact firstSecond.1.trans secondThird.1
  · intro reduct point
    exact (firstSecond.2 reduct point).trans (secondThird.2 reduct point)

/-- Selecting a different representative of one protected-behavior-and-cost
    class cannot change the target decision on any admissible model. -/
theorem same_class_selection_preserves_definition
    (admissible : Model → Prop)
    (shared : Model → Reduct Feature)
    (target : Model → Point → Prop)
    {first second : ChoiceCandidate Feature Point}
    (defines :
      ExplicitlyDefines admissible shared target first.predicate)
    (same : SameChoiceClass first second) :
    ExplicitlyDefines admissible shared target second.predicate := by
  intro model point modelAdmissible
  exact (same.2 (shared model) point).symm.trans
    (defines model point modelAdmissible)

/-- Two equal-cost minima that disagree on a protected observation are in
    different quotient classes. Mathematics establishes the fork; it does not
    appoint the authority allowed to select across it. -/
theorem inequivalent_minima_are_distinct_choice_classes
    {first second : ExplicitPredicate Feature Point}
    (nonUnique : NonUniqueMinimum first second)
    (cost : Nat) :
    ¬ SameChoiceClass
      { predicate := first, cost := cost }
      { predicate := second, cost := cost } := by
  intro same
  apply nonUnique.inequivalent
  exact propext (same.2 nonUnique.witnessReduct nonUnique.witnessPoint)

end InterpolantEnvelope
