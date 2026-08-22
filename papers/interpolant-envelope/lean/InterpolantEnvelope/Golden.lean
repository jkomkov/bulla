/-
  Golden Suite v0.1 abstraction bundle.

  These results justify the finite truth-table fallback, monotone anytime
  checkpoints, sufficient-but-not-necessarily-minimal evidence plans, and the
  closure-neighborhood epoch rule.  They do not verify Python parsing,
  resource accounting, signatures, or the benchmark generator.
-/

universe u v

namespace InterpolantEnvelope.Golden

/-- A feature vector is the complete Boolean observation of the declared
    finite shared vocabulary at one target tuple. -/
abbrev FeatureVector (n : Nat) := Fin n → Bool

/-- The canonical full-minterm fallback is the truth table itself.  The Python
    implementation renders every `true` vector as its full conjunction and
    disjoins those conjunctions. -/
def fullMintermFallback {n : Nat}
    (table : FeatureVector n → Bool) (vector : FeatureVector n) : Bool :=
  table vector

theorem full_minterm_fallback_sound {n : Nat}
    (table : FeatureVector n → Bool) (vector : FeatureVector n) :
    fullMintermFallback table vector = true ↔ table vector = true := by
  rfl

structure AnytimeEnvelope (World : Type u) where
  positive : World → Prop
  negative : World → Prop
  residual : World → Prop
  positive_negative_disjoint : ∀ world, ¬ (positive world ∧ negative world)
  positive_residual_disjoint : ∀ world, ¬ (positive world ∧ residual world)
  negative_residual_disjoint : ∀ world, ¬ (negative world ∧ residual world)

/-- More budget may certify terminal regions and remove residual worlds; it
    may never retract a certified terminal region. -/
def Refines {World : Type u}
    (prior next : AnytimeEnvelope World) : Prop :=
  (∀ world, prior.positive world → next.positive world) ∧
  (∀ world, prior.negative world → next.negative world) ∧
  (∀ world, next.residual world → prior.residual world)

theorem anytime_positive_monotone {World : Type u}
    {prior next : AnytimeEnvelope World} (h : Refines prior next) :
    ∀ world, prior.positive world → next.positive world := by
  exact h.1

theorem anytime_negative_monotone {World : Type u}
    {prior next : AnytimeEnvelope World} (h : Refines prior next) :
    ∀ world, prior.negative world → next.negative world := by
  exact h.2.1

theorem anytime_residual_antitone {World : Type u}
    {prior next : AnytimeEnvelope World} (h : Refines prior next) :
    ∀ world, next.residual world → prior.residual world := by
  exact h.2.2

theorem anytime_refines_trans {World : Type u}
    {first middle last : AnytimeEnvelope World}
    (h₁ : Refines first middle) (h₂ : Refines middle last) :
    Refines first last := by
  exact ⟨
    fun world positive => h₂.1 world (h₁.1 world positive),
    fun world negative => h₂.2.1 world (h₁.2.1 world negative),
    fun world residual => h₁.2.2 world (h₂.2.2 world residual)
  ⟩

/-- Minimality is irrelevant to safety: a plan is sufficient exactly when one
    selected observable separates every opposing pair. -/
def PlanSufficient {Offer : Type u} {Pair : Type v}
    (selected : Offer → Prop) (opposing : Pair → Prop)
    (separates : Offer → Pair → Prop) : Prop :=
  ∀ pair, opposing pair → ∃ offer, selected offer ∧ separates offer pair

theorem verified_plan_sufficient_without_minimality
    {Offer : Type u} {Pair : Type v}
    {selected : Offer → Prop} {opposing : Pair → Prop}
    {separates : Offer → Pair → Prop}
    (verified : PlanSufficient selected opposing separates) :
    ∀ pair, opposing pair → ∃ offer, selected offer ∧ separates offer pair := by
  exact verified

structure ClosureState where
  authorityHash : Nat
  neighborhoodHash : Nat
  semanticEpoch : Nat × Nat
  bindsEpoch : semanticEpoch = (authorityHash, neighborhoodHash)

theorem neighborhood_change_requires_new_epoch
    {prior next : ClosureState}
    (changedNeighborhood : prior.neighborhoodHash ≠ next.neighborhoodHash) :
    prior.semanticEpoch ≠ next.semanticEpoch := by
  intro sameEpoch
  have pairEq :
      (prior.authorityHash, prior.neighborhoodHash) =
      (next.authorityHash, next.neighborhoodHash) := by
    calc
      (prior.authorityHash, prior.neighborhoodHash) = prior.semanticEpoch := prior.bindsEpoch.symm
      _ = next.semanticEpoch := sameEpoch
      _ = (next.authorityHash, next.neighborhoodHash) := next.bindsEpoch
  have neighborhoodEq : prior.neighborhoodHash = next.neighborhoodHash := by
    exact congrArg Prod.snd pairEq
  exact changedNeighborhood neighborhoodEq

structure ReserveState (Outcome : Type u) where
  neighborhoodHash : Nat
  admissible : Outcome → Prop
  amount : Nat

def ReserveRefines {Outcome : Type u}
    (prior next : ReserveState Outcome) : Prop :=
  prior.neighborhoodHash = next.neighborhoodHash ∧
  (∀ outcome, next.admissible outcome → prior.admissible outcome) ∧
  next.amount ≤ prior.amount

theorem reserve_antitone_fixed_neighborhood {Outcome : Type u}
    {prior next : ReserveState Outcome} (h : ReserveRefines prior next) :
    next.amount ≤ prior.amount := by
  exact h.2.2

end InterpolantEnvelope.Golden
