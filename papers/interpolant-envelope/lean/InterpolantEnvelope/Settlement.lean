/-
  Semantic Settlement v0.1: authority-separated traces and reserve monotonicity.

  This is the protocol theorem, not an end-to-end verification of Python's
  parser, signatures, or witness log.  The executable abstraction bridge is
  tested separately by frozen vectors and the zero-import replay checker.
-/

universe u

namespace InterpolantEnvelope.Settlement

structure State (World : Type u) where
  epoch : Nat
  admissible : World → Prop

inductive Movement where
  | preserve
  | refine
  | revise
  | route
  deriving DecidableEq

structure Authority where
  refinement : Prop
  supersession : Prop

/-- The only state transitions exposed by the settlement profile.  Conflict is
    represented by `route`; it is not a fifth mutation. -/
inductive Step {World : Type u} :
    Authority → Movement → State World → State World → Prop where
  | preserve (authority) (state) : Step authority .preserve state state
  | refine (authority) (prior next)
      (authorized : authority.refinement)
      (sameEpoch : prior.epoch = next.epoch)
      (narrows : ∀ world, next.admissible world → prior.admissible world) :
      Step authority .refine prior next
  | revise (authority) (prior next)
      (authorized : authority.supersession)
      (newEpoch : prior.epoch ≠ next.epoch) :
      Step authority .revise prior next
  | route (authority) (state) : Step authority .route state state

def HonestStep {World : Type u}
    (authority : Authority) (movement : Movement)
    (prior next : State World) : Prop :=
  match movement with
  | .preserve => prior = next
  | .refine =>
      authority.refinement ∧ prior.epoch = next.epoch ∧
      ∀ world, next.admissible world → prior.admissible world
  | .revise => authority.supersession ∧ prior.epoch ≠ next.epoch
  | .route => prior = next

theorem step_is_honest {World : Type u}
    {authority : Authority} {movement : Movement}
    {prior next : State World}
    (step : Step authority movement prior next) :
    HonestStep authority movement prior next := by
  cases step with
  | preserve => rfl
  | refine _ _ authorized sameEpoch narrows =>
      exact ⟨authorized, sameEpoch, narrows⟩
  | revise _ _ authorized newEpoch => exact ⟨authorized, newEpoch⟩
  | route => rfl

/-- A trace carries every authored movement rather than erasing intermediate
    authority decisions. -/
inductive Trace {World : Type u} (authority : Authority) :
    State World → State World → Prop where
  | nil (state) : Trace authority state state
  | cons {first middle last movement}
      (step : Step authority movement first middle)
      (tail : Trace authority middle last) :
      Trace authority first last

inductive EveryStepHonest {World : Type u} (authority : Authority) :
    {first last : State World} → Trace authority first last → Prop where
  | nil (state) : EveryStepHonest authority (.nil state)
  | cons {first middle last movement}
      (step : Step authority movement first middle)
      (tail : Trace authority middle last)
      (honest : HonestStep authority movement first middle)
      (tailHonest : EveryStepHonest authority tail) :
      EveryStepHonest authority (.cons step tail)

/-- Answerable Ratchet trace theorem: every refinement in a trace is authorized
    and narrowing, every revision is supersession-authorized, and every route
    leaves the state unchanged. -/
theorem answerable_ratchet_trace {World : Type u}
    {authority : Authority} {first last : State World}
    (trace : Trace authority first last) :
    EveryStepHonest authority trace := by
  induction trace with
  | nil state => exact .nil state
  | cons step tail ih => exact .cons step tail (step_is_honest step) ih

/-- Conflict is a typed cause of ROUTE and cannot mutate the operative state. -/
def conflictRoute {World : Type u}
    (authority : Authority) (state : State World) :
    Step authority .route state state :=
  .route authority state

theorem conflict_non_mutation {World : Type u}
    (authority : Authority) (state : State World) :
    HonestStep authority .route state state := by
  exact step_is_honest (conflictRoute authority state)

/-- A segment containing refinements only: the formal meaning of “between
    authored revisions.” -/
inductive RefinementTrace {World : Type u} (authority : Authority) :
    State World → State World → Prop where
  | nil (state) : RefinementTrace authority state state
  | cons {first middle last}
      (step : Step authority .refine first middle)
      (tail : RefinementTrace authority middle last) :
      RefinementTrace authority first last

theorem refinement_monotone_between_revisions {World : Type u}
    {authority : Authority} {first last : State World}
    (trace : RefinementTrace authority first last) :
    first.epoch = last.epoch ∧
    ∀ world, last.admissible world → first.admissible world := by
  induction trace with
  | nil => exact ⟨rfl, fun _ h => h⟩
  | cons step tail ih =>
      cases step with
      | refine _ _ _ sameEpoch narrows =>
          exact ⟨sameEpoch.trans ih.1, fun world h => narrows world (ih.2 world h)⟩

/-- Reserve policies are acceptable only when their declared worst-case
    operator is antitone under inclusion of represented worlds. -/
structure ReservePolicy (World : Type u) where
  reserve : (World → Prop) → Nat
  antitone : ∀ prior next,
    (∀ world, next world → prior world) → reserve next ≤ reserve prior

theorem ambiguity_reserve_antitone_under_refinement {World : Type u}
    (policy : ReservePolicy World)
    {prior next : State World}
    (refines : ∀ world, next.admissible world → prior.admissible world) :
    policy.reserve next.admissible ≤ policy.reserve prior.admissible := by
  exact policy.antitone prior.admissible next.admissible refines

end InterpolantEnvelope.Settlement
