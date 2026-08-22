/-
  Assurance Linker 0.1: finite proof-carrying obligation model.

  This layer proves properties of the abstract finite transition system. It
  does not prove parser correctness, Bitcoin consensus, custody, denominator
  completeness, collectibility, or legal enforceability.
-/

import InterpolantEnvelope.ClaimFlow

namespace InterpolantEnvelope.AssuranceLinker

inductive Grounding where
  | selfAsserted
  | counterpartySigned
  | thirdPartyAnchored
  | executionVerified
  deriving DecidableEq

structure GroundingPolicy where
  /-- The promise author supplies any permitted narrowing relation. The model
      deliberately defines no universal total order over grounding classes. -/
  admissibleNarrowing : Grounding → Grounding → Prop

structure Claim where
  proposition : Nat
  grounding : Grounding
  authorityEpoch : Nat
  semanticEpoch : Nat

structure Guarantee where
  guarantor : Nat
  exposure : Nat
  recourse : Nat

/-- A derived claim either retains exact support, narrows standing, or adds a
    separate guarantor obligation while retaining the underlying claim. -/
inductive Derived (policy : GroundingPolicy) : Claim → Claim → Option Guarantee → Prop where
  | exact (claim : Claim) : Derived policy claim claim none
  | narrow (source : Claim) (grounding : Grounding)
      (standing : policy.admissibleNarrowing grounding source.grounding) :
      Derived policy source { source with grounding := grounding } none
  | guaranteed (claim : Claim) (guarantee : Guarantee) :
      Derived policy claim claim (some guarantee)

theorem claim_non_amplification
    (policy : GroundingPolicy)
    {source derived : Claim} {guarantee : Option Guarantee}
    (proof : Derived policy source derived guarantee) :
    derived.proposition = source.proposition ∧
    (derived.grounding = source.grounding ∨
      policy.admissibleNarrowing derived.grounding source.grounding) ∧
    derived.authorityEpoch = source.authorityEpoch ∧
    derived.semanticEpoch = source.semanticEpoch := by
  cases proof <;> simp_all

structure AssuranceFacts where
  predicate : Bool
  evidence : Bool
  coverage : Bool
  authority : Bool
  challengeClosed : Bool
  capital : Bool
  recourse : Bool
  semanticEpoch : Nat
  acceptedSemanticEpoch : Nat
  authorityEpoch : Nat
  acceptedAuthorityEpoch : Nat

def Eligible (facts : AssuranceFacts) : Prop :=
  facts.predicate = true ∧
  facts.evidence = true ∧
  facts.coverage = true ∧
  facts.authority = true ∧
  facts.challengeClosed = true ∧
  facts.capital = true ∧
  facts.recourse = true ∧
  facts.semanticEpoch = facts.acceptedSemanticEpoch ∧
  facts.authorityEpoch = facts.acceptedAuthorityEpoch

theorem settlement_soundness (facts : AssuranceFacts) (eligible : Eligible facts) :
    facts.predicate = true ∧
    facts.evidence = true ∧
    facts.coverage = true ∧
    facts.authority = true ∧
    facts.challengeClosed = true ∧
    facts.capital = true ∧
    facts.recourse = true := by
  exact ⟨eligible.1, eligible.2.1, eligible.2.2.1, eligible.2.2.2.1,
    eligible.2.2.2.2.1, eligible.2.2.2.2.2.1, eligible.2.2.2.2.2.2.1⟩

def withGuarantee (facts : AssuranceFacts) (capital recourse : Bool) :
    AssuranceFacts :=
  { facts with capital := capital, recourse := recourse }

theorem guarantee_separation (facts : AssuranceFacts) (capital recourse : Bool) :
    (withGuarantee facts capital recourse).evidence = facts.evidence ∧
    (withGuarantee facts capital recourse).predicate = facts.predicate ∧
    (withGuarantee facts capital recourse).authority = facts.authority := by
  simp [withGuarantee]

structure Allocation where
  amount : Nat
  active : Bool

def activeAmount (allocation : Allocation) : Nat :=
  if allocation.active then allocation.amount else 0

structure AcceptedPortfolio where
  locked : Nat
  allocations : List Allocation
  conserved : (allocations.map activeAmount).sum ≤ locked

theorem capital_conservation (portfolio : AcceptedPortfolio) :
    (portfolio.allocations.map activeAmount).sum ≤ portfolio.locked :=
  portfolio.conserved

def withholdEvidence (facts : AssuranceFacts) : AssuranceFacts :=
  { facts with evidence := false }

theorem withholding_monotonicity (facts : AssuranceFacts) :
    ¬ Eligible (withholdEvidence facts) := by
  intro eligible
  simp [Eligible, withholdEvidence] at eligible

structure AbstractRailFacts where
  lockBound : Bool
  reportedUnspent : Bool
  allocationAdequate : Bool
  declaredScopeExclusive : Bool
  checkpointCurrent : Bool

inductive RailKind where
  | fixtureEscrow
  | bitcoinRegtest
  | legalBond
  | internalReserve
  deriving DecidableEq

structure RailReport where
  kind : RailKind
  facts : AbstractRailFacts

def railNeutralCapital (report : RailReport) : Bool :=
  report.facts.lockBound &&
  report.facts.reportedUnspent &&
  report.facts.allocationAdequate &&
  report.facts.declaredScopeExclusive &&
  report.facts.checkpointCurrent

theorem rail_invariance
    (left right : RailReport)
    (sameFacts : left.facts = right.facts) :
    railNeutralCapital left = railNeutralCapital right := by
  simp [railNeutralCapital, sameFacts]

structure HistoricalVerdict where
  artifact : Nat
  eligible : Bool
  authorityEpoch : Nat
  semanticEpoch : Nat

structure Correction where
  prior : HistoricalVerdict
  replacement : Nat
  correctionReceipt : Nat

def appendCorrection (prior : HistoricalVerdict)
    (replacement correctionReceipt : Nat) : Correction :=
  ⟨prior, replacement, correctionReceipt⟩

theorem correction_non_erasure
    (prior : HistoricalVerdict) (replacement receipt : Nat) :
    (appendCorrection prior replacement receipt).prior = prior := by
  rfl

theorem semantic_epoch_safety
    (facts : AssuranceFacts)
    (stale : facts.semanticEpoch ≠ facts.acceptedSemanticEpoch) :
    ¬ Eligible facts := by
  intro eligible
  exact stale eligible.2.2.2.2.2.2.2.1

theorem authority_epoch_safety
    (facts : AssuranceFacts)
    (stale : facts.authorityEpoch ≠ facts.acceptedAuthorityEpoch) :
    ¬ Eligible facts := by
  intro eligible
  exact stale eligible.2.2.2.2.2.2.2.2

/-! ### Assurance Linker 0.2 accountability-circuit refinement -/

/-- The wire predicates are complementary comparisons over one exact expected
    digest and one exact supplied digest. They say nothing about worldly
    delivery or the semantic quality of the bytes. -/
def DigestConforms (expected supplied : Nat) : Prop := expected = supplied

def DigestMismatch (expected supplied : Nat) : Prop := expected ≠ supplied

theorem mismatch_conformance_exclusion (expected supplied : Nat) :
    ¬ (DigestConforms expected supplied ∧ DigestMismatch expected supplied) := by
  intro both
  exact both.2 both.1

theorem digest_relation_total (expected supplied : Nat) :
    DigestConforms expected supplied ∨ DigestMismatch expected supplied := by
  by_cases relation : expected = supplied
  · exact Or.inl relation
  · exact Or.inr relation

/-- Finite premises for the bounded P2 remedy. Witness facts are deliberately
    distinct from deterministic evidence and from consequence authority. -/
structure WitnessedRecourseFacts where
  mismatch : Bool
  deterministicClass : Bool
  receiptIntegrity : Bool
  receiverBinding : Bool
  inclusion : Bool
  historyConsistency : Bool
  witnessTrusted : Bool
  openWitnessed : Bool
  expiryReferencesOpen : Bool
  deadlineReached : Bool
  witnessOrder : Bool
  capitalAdequate : Bool
  consequenceAuthority : Bool
  exactAuthorization : Bool

def RemedyEligible (facts : WitnessedRecourseFacts) : Prop :=
  facts.mismatch = true ∧
  facts.deterministicClass = true ∧
  facts.receiptIntegrity = true ∧
  facts.receiverBinding = true ∧
  facts.inclusion = true ∧
  facts.historyConsistency = true ∧
  facts.witnessTrusted = true ∧
  facts.openWitnessed = true ∧
  facts.expiryReferencesOpen = true ∧
  facts.deadlineReached = true ∧
  facts.witnessOrder = true ∧
  facts.capitalAdequate = true

def SettlementAttemptAccepted (facts : WitnessedRecourseFacts) : Prop :=
  RemedyEligible facts ∧
  facts.consequenceAuthority = true ∧
  facts.exactAuthorization = true

theorem witnessed_recourse_soundness
    (facts : WitnessedRecourseFacts)
    (accepted : SettlementAttemptAccepted facts) :
    facts.mismatch = true ∧
    facts.deterministicClass = true ∧
    facts.receiptIntegrity = true ∧
    facts.receiverBinding = true ∧
    facts.inclusion = true ∧
    facts.historyConsistency = true ∧
    facts.witnessTrusted = true ∧
    facts.openWitnessed = true ∧
    facts.expiryReferencesOpen = true ∧
    facts.deadlineReached = true ∧
    facts.witnessOrder = true ∧
    facts.capitalAdequate = true ∧
    facts.consequenceAuthority = true ∧
    facts.exactAuthorization = true := by
  rcases accepted with ⟨eligible, authority, exact⟩
  rcases eligible with ⟨mismatch, deterministic, receipt, receiver, inclusion,
    consistency, trusted, opened, expiry, deadline, ordered, capital⟩
  exact ⟨mismatch, deterministic, receipt, receiver, inclusion, consistency,
    trusted, opened, expiry, deadline, ordered, capital, authority, exact⟩

theorem open_challenge_blocks_remedy
    (facts : WitnessedRecourseFacts)
    (openChallenge : facts.expiryReferencesOpen = false) :
    ¬ RemedyEligible facts := by
  simp [RemedyEligible, openChallenge]

theorem early_expiry_blocks_remedy
    (facts : WitnessedRecourseFacts)
    (early : facts.deadlineReached = false) :
    ¬ RemedyEligible facts := by
  simp [RemedyEligible, early]

theorem unordered_history_blocks_remedy
    (facts : WitnessedRecourseFacts)
    (unordered : facts.witnessOrder = false) :
    ¬ RemedyEligible facts := by
  simp [RemedyEligible, unordered]

inductive DisputeClass where
  | deterministicP2
  | appraisalP3
  | semanticP4
  deriving DecidableEq

def AutomaticRemedyClass (dispute : DisputeClass) : Prop :=
  dispute = .deterministicP2

theorem appraisal_cannot_auto_remedy :
    ¬ AutomaticRemedyClass .appraisalP3 := by
  simp [AutomaticRemedyClass]

theorem semantic_cannot_auto_remedy :
    ¬ AutomaticRemedyClass .semanticP4 := by
  simp [AutomaticRemedyClass]

/-! ### Bonded witness covenant 0.1 refinement -/

structure SignedCheckpointView where
  operator : Nat
  log : Nat
  authorityEpoch : Nat
  treeSize : Nat
  root : Nat
  authentic : Bool
  jointlyObserved : Bool

def ComparableViews (left right : SignedCheckpointView) : Prop :=
  left.operator = right.operator ∧
  left.log = right.log ∧
  left.authorityEpoch = right.authorityEpoch ∧
  left.treeSize = right.treeSize

def SameSizeEquivocation (left right : SignedCheckpointView) : Prop :=
  ComparableViews left right ∧
  left.authentic = true ∧
  right.authentic = true ∧
  left.jointlyObserved = true ∧
  right.jointlyObserved = true ∧
  left.root ≠ right.root

def ConsistentComparedHistory (left right : SignedCheckpointView) : Prop :=
  ComparableViews left right ∧
  left.authentic = true ∧
  right.authentic = true ∧
  left.jointlyObserved = true ∧
  right.jointlyObserved = true ∧
  left.root = right.root

theorem consistent_history_equivocation_exclusion
    (left right : SignedCheckpointView) :
    ¬ (ConsistentComparedHistory left right ∧ SameSizeEquivocation left right) := by
  intro both
  exact both.2.2.2.2.2.2 both.1.2.2.2.2.2

theorem one_view_cannot_establish_equivocation
    (left right : SignedCheckpointView)
    (missing : right.jointlyObserved = false) :
    ¬ SameSizeEquivocation left right := by
  intro fault
  simp [SameSizeEquivocation, missing] at fault

structure WitnessCovenantFacts where
  objectiveFault : Bool
  challengeClosed : Bool
  dedicatedAllocationAdequate : Bool
  authorityAccepted : Bool
  exactAuthorization : Bool
  witnessTrusted : Bool
  historyAuthenticated : Bool
  substantiveClaimGrounded : Bool

def WitnessRemedyEligible (facts : WitnessCovenantFacts) : Prop :=
  facts.objectiveFault = true ∧
  facts.challengeClosed = true ∧
  facts.dedicatedAllocationAdequate = true

def WitnessRemedyAuthorized (facts : WitnessCovenantFacts) : Prop :=
  WitnessRemedyEligible facts ∧
  facts.authorityAccepted = true ∧
  facts.exactAuthorization = true

theorem witness_remedy_eligibility_soundness
    (facts : WitnessCovenantFacts)
    (eligible : WitnessRemedyEligible facts) :
    facts.objectiveFault = true ∧
    facts.challengeClosed = true ∧
    facts.dedicatedAllocationAdequate = true :=
  eligible

theorem witness_remedy_soundness
    (facts : WitnessCovenantFacts)
    (authorized : WitnessRemedyAuthorized facts) :
    facts.objectiveFault = true ∧
    facts.challengeClosed = true ∧
    facts.dedicatedAllocationAdequate = true ∧
    facts.authorityAccepted = true ∧
    facts.exactAuthorization = true := by
  exact ⟨authorized.1.1, authorized.1.2.1, authorized.1.2.2,
    authorized.2.1, authorized.2.2⟩

theorem witness_open_challenge_blocks_remedy
    (facts : WitnessCovenantFacts)
    (openChallenge : facts.challengeClosed = false) :
    ¬ WitnessRemedyEligible facts := by
  simp [WitnessRemedyEligible, openChallenge]

theorem missing_witness_bond_blocks_remedy
    (facts : WitnessCovenantFacts)
    (missing : facts.dedicatedAllocationAdequate = false) :
    ¬ WitnessRemedyEligible facts := by
  simp [WitnessRemedyEligible, missing]

theorem model_identity_cannot_auto_witness_remedy :
    ¬ AutomaticRemedyClass .appraisalP3 :=
  appraisal_cannot_auto_remedy

def withWitnessBond (facts : WitnessCovenantFacts)
    (adequate : Bool) : WitnessCovenantFacts :=
  { facts with dedicatedAllocationAdequate := adequate }

theorem witness_bond_separation
    (facts : WitnessCovenantFacts) (adequate : Bool) :
    (withWitnessBond facts adequate).witnessTrusted = facts.witnessTrusted ∧
    (withWitnessBond facts adequate).historyAuthenticated = facts.historyAuthenticated ∧
    (withWitnessBond facts adequate).substantiveClaimGrounded =
      facts.substantiveClaimGrounded := by
  simp [withWitnessBond]

theorem witness_withholding_cannot_improve_fault
    (left right : SignedCheckpointView)
    (withheld : right.jointlyObserved = false) :
    ¬ SameSizeEquivocation left right :=
  one_view_cannot_establish_equivocation left right withheld

/-! ### Answerability Network 0.1 composition -/

inductive RelianceDisposition where
  | affected
  | notAffected
  | undetermined
  deriving DecidableEq

structure DeclaredRelianceFacts where
  declaredPath : Bool
  ancestryComplete : Bool
  witnessFault : Bool

def relianceDisposition (facts : DeclaredRelianceFacts) : RelianceDisposition :=
  if facts.witnessFault && facts.declaredPath then .affected
  else if facts.ancestryComplete then .notAffected
  else .undetermined

theorem declared_path_from_witness_fault_requires_recheck
    (facts : DeclaredRelianceFacts)
    (fault : facts.witnessFault = true)
    (path : facts.declaredPath = true) :
    relianceDisposition facts = .affected := by
  simp [relianceDisposition, fault, path]

theorem incomplete_lineage_cannot_clear
    (facts : DeclaredRelianceFacts)
    (noPath : facts.declaredPath = false)
    (incomplete : facts.ancestryComplete = false) :
    relianceDisposition facts = .undetermined := by
  simp [relianceDisposition, noPath, incomplete]

theorem complete_unrelated_branch_remains_unaffected
    (facts : DeclaredRelianceFacts)
    (noPath : facts.declaredPath = false)
    (complete : facts.ancestryComplete = true) :
    relianceDisposition facts = .notAffected := by
  simp [relianceDisposition, noPath, complete]

theorem witness_fault_does_not_strengthen_substantive_claim
    (facts : WitnessCovenantFacts) (fault : Bool) :
    ({ facts with objectiveFault := fault }).substantiveClaimGrounded =
      facts.substantiveClaimGrounded := by
  simp

structure TestLedgerFacts where
  attemptReported : Bool
  executionObserved : Bool
  collectionObserved : Bool

def reportAttempt (facts : TestLedgerFacts) : TestLedgerFacts :=
  { facts with attemptReported := true }

theorem attempt_report_separation (facts : TestLedgerFacts) :
    (reportAttempt facts).executionObserved = facts.executionObserved ∧
    (reportAttempt facts).collectionObserved = facts.collectionObserved := by
  simp [reportAttempt]

end InterpolantEnvelope.AssuranceLinker
