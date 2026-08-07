/-!
Finite abstract model for bulla.inference-clearing/0.1-experimental.

This proves properties of the closed relation and consequence guards. It does
not prove parser correctness, the quality of a model, historical provider
execution, denominator completeness, settlement collectibility, or worldly
truth.
-/

namespace InferenceClearing

structure RelationEvidence where
  outputBound : Prop
  modelTermBound : Prop
  relationReproduced : Prop

def RelationEstablished (e : RelationEvidence) : Prop :=
  e.outputBound ∧ e.modelTermBound ∧ e.relationReproduced

theorem relation_soundness (e : RelationEvidence) (h : RelationEstablished e) :
    e.outputBound ∧ e.modelTermBound ∧ e.relationReproduced := h

structure ClaimStanding where
  relation : Prop
  historicalExecution : Prop
  capital : Prop
  recourse : Prop

def addGuarantee (standing : ClaimStanding) (capital recourse : Prop) : ClaimStanding :=
  { standing with capital := capital, recourse := recourse }

theorem historical_execution_non_amplification
    (standing : ClaimStanding) (capital recourse : Prop) :
    (addGuarantee standing capital recourse).historicalExecution ↔
      standing.historicalExecution := by
  rfl

structure Premises where
  terms : Prop
  authority : Prop
  relation : Prop
  coverage : Prop
  witness : Prop
  capital : Prop

def Eligible (p : Premises) : Prop :=
  p.terms ∧ p.authority ∧ p.relation ∧ p.coverage ∧ p.witness ∧ p.capital

theorem evidence_removal_monotonicity (p : Premises) (h : ¬ p.relation) :
    ¬ Eligible p := by
  intro eligible
  exact h eligible.2.2.1

theorem coverage_sensitivity (p : Premises) (h : ¬ p.coverage) :
    ¬ Eligible p := by
  intro eligible
  exact h eligible.2.2.2.1

def Authorized (p : Premises) (authorityGrant : Prop) : Prop :=
  Eligible p ∧ authorityGrant

def Executed (p : Premises) (authorityGrant railExecution : Prop) : Prop :=
  Authorized p authorityGrant ∧ railExecution

theorem authorization_requires_eligibility
    (p : Premises) (authorityGrant : Prop)
    (h : Authorized p authorityGrant) : Eligible p := h.1

theorem execution_requires_authorization
    (p : Premises) (authorityGrant railExecution : Prop)
    (h : Executed p authorityGrant railExecution) :
    Authorized p authorityGrant := h.1

end InferenceClearing
