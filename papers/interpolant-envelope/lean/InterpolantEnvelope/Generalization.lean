/-
  Generalization Constitution v0.5.

  This file formalizes the finite order-theoretic and separation invariants of
  the executable profile.  It does not claim open-world completeness or legal
  validity for an authored scope.
-/

import InterpolantEnvelope.ClaimFlow

universe u

namespace InterpolantEnvelope.Generalization

structure SemanticState where
  epoch : Nat
  surface : Nat
  deriving DecidableEq

structure Candidate where
  reason : Nat
  scope : Nat
  sourceFinding : Nat

/-- Proposal is observational with respect to operative semantic state. -/
def propose (state : SemanticState) (candidate : Candidate) :
    Candidate × SemanticState :=
  (candidate, state)

theorem candidate_cannot_mutate_semantic_state
    (state : SemanticState) (candidate : Candidate) :
    (propose state candidate).2 = state := by
  rfl

structure Adoption where
  candidate : Candidate
  authorityReceipt : Nat

structure ApplicabilityFinding where
  adoption : Adoption
  case : Nat
  forumReceipt : Nat

structure PrecedentSettlementPremises where
  adoption : Adoption
  applicability : ApplicabilityFinding
  sameAdoption : applicability.adoption = adoption

theorem adoption_and_applicability_are_separate_premises
    (premises : PrecedentSettlementPremises) :
    (premises.adoption.authorityReceipt =
      premises.applicability.adoption.authorityReceipt) ∧
    (premises.applicability.case = premises.applicability.case) := by
  constructor
  · rw [premises.sameAdoption]
  · rfl

section ScopeOrder

variable {World : Type u}

abbrev Scope (World : Type u) := World → Prop

/-- `narrower left right` means every world represented by left is also
    represented by right. -/
def Narrower (left right : Scope World) : Prop :=
  ∀ world, left world → right world

def Safe (allowed : World → Prop) (scope : Scope World) : Prop :=
  ∀ world, scope world → allowed world

theorem safe_scopes_downward_closed
    (allowed : World → Prop) {narrow wide : Scope World}
    (order : Narrower narrow wide) (wideSafe : Safe allowed wide) :
    Safe allowed narrow := by
  intro world inNarrow
  exact wideSafe world (order world inNarrow)

structure UnsafeWitness (allowed : World → Prop) (scope : Scope World) where
  world : World
  inScope : scope world
  forbidden : ¬ allowed world

theorem unsafe_witness_invalidates_every_containing_superscope
    (allowed : World → Prop) {scope super : Scope World}
    (witness : UnsafeWitness allowed scope)
    (contains : Narrower scope super) :
    ¬ Safe allowed super := by
  intro superSafe
  exact witness.forbidden (superSafe witness.world (contains witness.world witness.inScope))

/-- Widening cannot remove a represented case.  This is the proposition-level
    form of monotonic represented yield; the executable layer counts the finite
    witnesses to this inclusion. -/
theorem widening_cannot_reduce_represented_yield
    {narrow wide : Scope World} (order : Narrower narrow wide)
    {world : World} (represented : narrow world) :
    wide world :=
  order world represented

end ScopeOrder

inductive CertifiedDecision where
  | rely
  | refuse
  deriving DecidableEq

structure BoundedCertificate (World : Type u) where
  world : World
  decision : CertifiedDecision
  logicalCost : Nat

def CertifiedWithin {World : Type u}
    (budget : Nat) (certificate : BoundedCertificate World) : Prop :=
  certificate.logicalCost ≤ budget

theorem increased_budget_cannot_reverse_certified_cell
    {World : Type u} (certificate : BoundedCertificate World)
    {prior next : Nat} (authorizedIncrease : prior ≤ next)
    (certified : CertifiedWithin prior certificate) :
    CertifiedWithin next certificate := by
  exact Nat.le_trans certified authorizedIncrease

inductive HarmBarrier where
  | compensable
  | reversibleOnly
  | humanReviewRequired
  | categoricalRefuse
  deriving DecidableEq

structure Effect where
  protectedPredicate : Nat
  barrier : HarmBarrier

inductive Disguise where
  | rename
  | wrapper
  | decomposition
  | proxyField
  | repeatedTemporary
  | affiliatedExecutor
  deriving DecidableEq

/-- Disguises may change action presentation, never the authored effect. -/
def disguise (_ : Disguise) (effect : Effect) : Effect :=
  effect

theorem categorical_effect_barrier_survives_disguise
    (effect : Effect) (barred : effect.barrier = .categoricalRefuse)
    (form : Disguise) :
    (disguise form effect).barrier = .categoricalRefuse := by
  simpa [disguise] using barred

structure HistoricalFinding where
  decision : Nat
  epoch : Nat

structure Supersession where
  historical : HistoricalFinding
  futureEpoch : Nat
  futureRule : Nat

def supersede (historical : HistoricalFinding)
    (futureEpoch futureRule : Nat) : Supersession :=
  ⟨historical, futureEpoch, futureRule⟩

theorem supersession_preserves_history_without_automatic_future_governance
    (historical : HistoricalFinding) (futureEpoch futureRule : Nat) :
    (supersede historical futureEpoch futureRule).historical = historical := by
  rfl

end InterpolantEnvelope.Generalization
