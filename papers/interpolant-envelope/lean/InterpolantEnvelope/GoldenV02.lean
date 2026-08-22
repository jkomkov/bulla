/-
  Golden Gate v0.2 checked-compression abstraction.

  This file proves the trust-boundary facts used by the finite cube generator:
  literal removal is accepted only after the generalized cube is checked
  against every opposite vector; a sufficient cover is sound independently of
  minimality; and truncating to checked positive/negative cube prefixes cannot
  create overlap. It does not verify Python enumeration or resource accounting.
-/

universe u

namespace InterpolantEnvelope.GoldenV02

abbrev FeatureVector (n : Nat) := Fin n → Bool
abbrev Cube (n : Nat) := Fin n → Option Bool

def Matches {n : Nat} (cube : Cube n) (vector : FeatureVector n) : Prop :=
  ∀ index expected, cube index = some expected → vector index = expected

def SafeAgainst {n : Nat}
    (cube : Cube n) (opposite : FeatureVector n → Prop) : Prop :=
  ∀ vector, opposite vector → ¬ Matches cube vector

/-- The implementation may drop a literal only after checking the resulting
    cube against the complete opposite-vector set. The order of attempted
    literal drops has no role in the theorem. -/
theorem literal_dropping_sound {n : Nat}
    {generalized : Cube n} {opposite : FeatureVector n → Prop}
    (checked : ∀ vector, opposite vector → ¬ Matches generalized vector) :
    SafeAgainst generalized opposite := by
  exact checked

def CoveredBy {n : Nat} (cubes : List (Cube n)) (vector : FeatureVector n) : Prop :=
  ∃ cube, cube ∈ cubes ∧ Matches cube vector

def CubeCoverSound {n : Nat}
    (cubes : List (Cube n))
    (positive negative : FeatureVector n → Prop) : Prop :=
  (∀ vector, positive vector → CoveredBy cubes vector) ∧
  (∀ cube, cube ∈ cubes → SafeAgainst cube negative)

/-- A verified cube cover accepts every declared positive vector and no
    declared negative vector. Minimality is not a premise. -/
theorem sufficient_cube_cover_sound {n : Nat}
    {cubes : List (Cube n)} {positive negative : FeatureVector n → Prop}
    (verified : CubeCoverSound cubes positive negative) :
    (∀ vector, positive vector → CoveredBy cubes vector) ∧
    (∀ vector, negative vector → ¬ CoveredBy cubes vector) := by
  constructor
  · exact verified.1
  · intro vector hnegative hcovered
    obtain ⟨cube, hmember, hmatches⟩ := hcovered
    exact verified.2 cube hmember vector hnegative hmatches

/-- Any subcover of individually safe cubes remains safe. This is the formal
    basis for bounded-prefix emission when the full cover exceeds the AST cap. -/
theorem truncation_preserves_safety {n : Nat}
    {full truncated : List (Cube n)} {negative : FeatureVector n → Prop}
    (subset : ∀ cube, cube ∈ truncated → cube ∈ full)
    (safe : ∀ cube, cube ∈ full → SafeAgainst cube negative) :
    ∀ vector, negative vector → ¬ CoveredBy truncated vector := by
  intro vector hnegative hcovered
  obtain ⟨cube, hmember, hmatches⟩ := hcovered
  exact safe cube (subset cube hmember) vector hnegative hmatches

/-- Independently safe positive and negative cube regions are disjoint when
    each side treats the other side's complete vectors as opposites. -/
theorem truncated_regions_disjoint {n : Nat}
    {positiveCubes negativeCubes : List (Cube n)}
    (positiveSafe : ∀ cube, cube ∈ positiveCubes →
      SafeAgainst cube (fun vector => CoveredBy negativeCubes vector)) :
    ∀ vector, ¬ (CoveredBy positiveCubes vector ∧ CoveredBy negativeCubes vector) := by
  intro vector regions
  obtain ⟨positiveCube, hmember, hmatches⟩ := regions.1
  exact positiveSafe positiveCube hmember vector regions.2 hmatches

end InterpolantEnvelope.GoldenV02
