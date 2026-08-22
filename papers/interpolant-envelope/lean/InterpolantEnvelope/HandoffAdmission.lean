/-
  Handoff Admission 0.1: finite abstract receiver-admission model.

  This file checks only the finite logical core mirrored by the executable
  fixtures. It does not prove parser correctness, signature implementations,
  transport delivery, revocation-registry completeness, worldly truth,
  downstream action safety, or organizational independence.
-/

namespace InterpolantEnvelope.HandoffAdmission

universe u

/-- The closed capability carrier used by the alpha. -/
structure Scope where
  services : Nat → Prop
  actions : Nat → Prop
  resources : Nat → Prop

/-- A child capability attenuates its parent when no closed permission set grows. -/
def Attenuates (child parent : Scope) : Prop :=
  (∀ value, child.services value → parent.services value) ∧
  (∀ value, child.actions value → parent.actions value) ∧
  (∀ value, child.resources value → parent.resources value)

theorem attenuation_reflexive (scope : Scope) : Attenuates scope scope := by
  exact ⟨fun _ h => h, fun _ h => h, fun _ h => h⟩

theorem attenuation_transitive {leaf middle root : Scope}
    (h₀ : Attenuates leaf middle) (h₁ : Attenuates middle root) :
    Attenuates leaf root := by
  rcases h₀ with ⟨hs₀, ha₀, hr₀⟩
  rcases h₁ with ⟨hs₁, ha₁, hr₁⟩
  exact ⟨fun value h => hs₁ value (hs₀ value h),
    fun value h => ha₁ value (ha₀ value h),
    fun value h => hr₁ value (hr₀ value h)⟩

/-- All worlds compatible with the retained observations produce one decision. -/
def DecisionSufficient {World : Type u} (compatible : World → Prop)
    (decision : World → Bool) : Prop :=
  ∀ left, compatible left → ∀ right, compatible right → decision left = decision right

/-- The negative certificate emitted by the executable assessor. -/
def DecisionCounterexample {World : Type u} (compatible : World → Prop)
    (decision : World → Bool) (left right : World) : Prop :=
  compatible left ∧ compatible right ∧ decision left ≠ decision right

theorem counterexample_refutes_sufficiency {World : Type u}
    (compatible : World → Prop) (decision : World → Bool)
    (left right : World)
    (certificate : DecisionCounterexample compatible decision left right) :
    ¬ DecisionSufficient compatible decision := by
  intro sufficient
  exact certificate.2.2 (sufficient left certificate.1 right certificate.2.1)

/-- More retained information narrows the compatible-world set. If the wider
    set was decision-sufficient, its narrowed subset remains sufficient. -/
theorem sufficiency_preserved_by_narrowing {World : Type u}
    {narrow wide : World → Prop} (decision : World → Bool)
    (subset : ∀ world, narrow world → wide world)
    (sufficient : DecisionSufficient wide decision) :
    DecisionSufficient narrow decision := by
  intro left hleft right hright
  exact sufficient left (subset left hleft) right (subset right hright)

structure AdmissionFacts where
  integrity : Bool
  transportBound : Bool
  receiverAccepted : Bool
  capabilityCurrent : Bool
  observationsAccepted : Bool
  decisionSufficient : Bool
  policyProceeds : Bool
  authorityEpochCurrent : Bool
  semanticEpochCurrent : Bool

def Admissible (facts : AdmissionFacts) : Prop :=
  facts.integrity = true ∧
  facts.transportBound = true ∧
  facts.receiverAccepted = true ∧
  facts.capabilityCurrent = true ∧
  facts.observationsAccepted = true ∧
  facts.decisionSufficient = true ∧
  facts.policyProceeds = true ∧
  facts.authorityEpochCurrent = true ∧
  facts.semanticEpochCurrent = true

theorem admission_soundness (facts : AdmissionFacts) (admitted : Admissible facts) :
    facts.integrity = true ∧
    facts.capabilityCurrent = true ∧
    facts.observationsAccepted = true ∧
    facts.decisionSufficient = true ∧
    facts.policyProceeds = true := by
  exact ⟨admitted.1, admitted.2.2.2.1, admitted.2.2.2.2.1,
    admitted.2.2.2.2.2.1, admitted.2.2.2.2.2.2.1⟩

def removeDecisionEvidence (facts : AdmissionFacts) : AdmissionFacts :=
  { facts with decisionSufficient := false }

theorem evidence_removal_cannot_admit (facts : AdmissionFacts) :
    ¬ Admissible (removeDecisionEvidence facts) := by
  intro admitted
  simp [Admissible, removeDecisionEvidence] at admitted

/-- Declared reachability in the append-only reliance graph. -/
inductive Reaches {Node : Type u} (edge : Node → Node → Prop) : Node → Node → Prop where
  | refl (node : Node) : Reaches edge node node
  | step {source middle target : Node} :
      edge source middle → Reaches edge middle target → Reaches edge source target

/-- A correction with an exact declared path affects its target. The theorem is
    intentionally about recall, not rollback or substantive truth. -/
theorem correction_path_is_affected {Node : Type u} (edge : Node → Node → Prop)
    (corrected target : Node) (path : Reaches edge corrected target) :
    ∃ witness, Reaches edge corrected witness ∧ witness = target := by
  exact ⟨target, path, rfl⟩

end InterpolantEnvelope.HandoffAdmission
