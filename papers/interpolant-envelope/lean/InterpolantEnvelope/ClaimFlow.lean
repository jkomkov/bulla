/-
  Claim Flow v0.4: authority provenance and no-free-precedent results.

  This is a faithful finite abstraction of the executable profile.  It proves
  inversion/provenance properties; it does not call them language progress.
-/

import InterpolantEnvelope.Boundary

universe u v

namespace InterpolantEnvelope.ClaimFlow

inductive Permission where
  | appraise
  | forumFinding
  | adoptPrecedent
  | settle
  deriving DecidableEq

structure AuthorityToken where
  permission : Permission
  receipt : Nat

inductive ClaimKind where
  | evidence
  | institutionalFact
  | precedent
  | settlement
  deriving DecidableEq

structure Claim where
  kind : ClaimKind
  epoch : Nat
  scope : Nat

/-- Every derived institutional artifact is introduced only by the constructor
    carrying the corresponding proposition-specific authority token. -/
inductive Derives : Claim → AuthorityToken → Prop where
  | appraise (epoch scope receipt) :
      Derives ⟨.evidence, epoch, scope⟩ ⟨.appraise, receipt⟩
  | forumFinding (epoch scope receipt) :
      Derives ⟨.institutionalFact, epoch, scope⟩ ⟨.forumFinding, receipt⟩
  | adoptPrecedent (epoch scope receipt) :
      Derives ⟨.precedent, epoch, scope⟩ ⟨.adoptPrecedent, receipt⟩
  | settle (epoch scope receipt) :
      Derives ⟨.settlement, epoch, scope⟩ ⟨.settle, receipt⟩

theorem no_borrowed_authority
    {claim : Claim} {token : AuthorityToken}
    (derivation : Derives claim token) :
    (claim.kind = .evidence → token.permission = .appraise) ∧
    (claim.kind = .institutionalFact → token.permission = .forumFinding) ∧
    (claim.kind = .precedent → token.permission = .adoptPrecedent) ∧
    (claim.kind = .settlement → token.permission = .settle) := by
  cases derivation <;> simp

/-- Finite case labels on a proper subset admit two total extensions which
    agree on every labelled case and disagree on an unseen case.  Therefore
    labels alone do not determine a fresh case absent a hypothesis/reason
    restriction. -/
theorem no_free_precedent
    {Case : Type u}
    (seen : Case → Prop)
    [DecidablePred seen]
    (proper : ∃ unseen, ¬ seen unseen) :
    ∃ first second : Case → Bool,
      (∀ c, seen c → first c = second c) ∧
      ∃ unseen, ¬ seen unseen ∧ first unseen ≠ second unseen := by
  let first : Case → Bool := fun _ => false
  let second : Case → Bool := fun c => if seen c then false else true
  refine ⟨first, second, ?_, ?_⟩
  · intro c hSeen
    simp [first, second, hSeen]
  · obtain ⟨unseen, hUnseen⟩ := proper
    refine ⟨unseen, hUnseen, ?_⟩
    simp [first, second, hUnseen]

inductive PrecedentEffect where
  | caseOnly
  | persuasive
  | bindingWithinScope
  deriving DecidableEq

structure ForumFinding (Case : Type u) where
  boundCase : Case
  epoch : Nat

structure PrecedentRule (Case : Type u) where
  source : ForumFinding Case
  effect : PrecedentEffect
  reason : Case → Prop
  applicability : Case → Prop
  authority : AuthorityToken
  authorityCorrect : authority.permission = .adoptPrecedent

def Applies {Case : Type u} (rule : PrecedentRule Case) (candidate : Case) : Prop :=
  match rule.effect with
  | .caseOnly => candidate = rule.source.boundCase
  | .persuasive => False
  | .bindingWithinScope => rule.reason candidate ∧ rule.applicability candidate

theorem case_only_finding_cannot_derive_general_precedent
    {Case : Type u} (rule : PrecedentRule Case)
    (caseOnly : rule.effect = .caseOnly)
    {candidate : Case}
    (different : candidate ≠ rule.source.boundCase) :
    ¬ Applies rule candidate := by
  simp [Applies, caseOnly, different]

theorem binding_precedent_only_inside_declared_reason_and_scope
    {Case : Type u} (rule : PrecedentRule Case)
    (binding : rule.effect = .bindingWithinScope)
    {candidate : Case}
    (applies : Applies rule candidate) :
    rule.reason candidate ∧ rule.applicability candidate := by
  simpa [Applies, binding] using applies

/-- The executable same-epoch rule admits only trace refinements; reuse of the
    existing boundary theorem gives preservation of prior RELY and REFUSE. -/
theorem same_epoch_precedent_refinement_preserves_surfaces
    {World : Type u}
    {prior next : World → Boundary.Decision}
    (refines : Boundary.TraceRefines prior next) :
    (∀ world, prior world = .rely → next world = .rely) ∧
    (∀ world, prior world = .refuse → next world = .refuse) :=
  Boundary.trace_refinement_preserves_decided_surfaces refines

structure HistoricalDecision where
  decision : Nat
  epoch : Nat

structure Review where
  historical : HistoricalDecision
  reviewEpoch : Nat
  remedy : Nat

def appendReview (historical : HistoricalDecision)
    (reviewEpoch remedy : Nat) : Review :=
  ⟨historical, reviewEpoch, remedy⟩

theorem supersession_preserves_historical_decision
    (historical : HistoricalDecision) (newEpoch remedy : Nat) :
    (appendReview historical newEpoch remedy).historical = historical := by
  rfl

structure DerivationBudget where
  epoch : Nat
  branches : Nat

def increaseBudget (budget : DerivationBudget) (additional : Nat) : DerivationBudget :=
  ⟨budget.epoch, budget.branches + additional⟩

theorem increasing_budget_does_not_change_semantic_epoch
    (budget : DerivationBudget) (additional : Nat) :
    (increaseBudget budget additional).epoch = budget.epoch := by
  rfl

/-- A closed verified trace stores one authority-bearing derivation per edge. -/
inductive VerifiedTrace : List (Claim × AuthorityToken) → Prop where
  | nil : VerifiedTrace []
  | cons {claim token rest}
      (head : Derives claim token)
      (tail : VerifiedTrace rest) :
      VerifiedTrace ((claim, token) :: rest)

theorem verified_trace_append
    {first second : List (Claim × AuthorityToken)}
    (left : VerifiedTrace first)
    (right : VerifiedTrace second) :
    VerifiedTrace (first ++ second) := by
  induction left with
  | nil => simpa using right
  | cons head tail ih =>
      exact .cons head ih

end InterpolantEnvelope.ClaimFlow
