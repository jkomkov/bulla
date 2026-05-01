# Theorem Targets for "Local Interface Descriptions Do Not Compose"

## Theorem 1: Non-Locality of Hiddenness

**Statement.** Let $\mathcal{T} = \{T_1, \ldots, T_n\}$ be a set of tools with observable schemas $\sigma(T_i) \subseteq S(T_i)$, where $S(T_i)$ is the full (internal) state space. Define the composition graph $G(\mathcal{T})$ with vertices $\mathcal{T}$ and edges whenever two tools share a convention dimension. The hidden set $H(T_i, G) = S(T_i) \setminus \sigma(T_i)$ is the set of fields whose convention is not determined by the schema.

**A field's compositional relevance depends on the graph, not the field alone:**

For any tool $T_i$ with hidden field $f \in H(T_i, G)$, the field $f$ is a *blind spot* in composition $G$ if and only if there exists an edge $e = (T_i, T_j)$ in $G$ such that the dimension containing $f$ also appears in $T_j$'s hidden set.

**Consequence:** Whether a hidden field matters for composition is not determinable from $T_i$'s schema alone. It requires knowledge of the composition partner.

**Constructive proof (from synthetic ecology):**

There exist tools $T_A, T_B, T_C$ such that:
- $H(T_A) = \{\texttt{path}\}$
- In composition $\{T_A, T_B\}$: $\texttt{path}$ is a blind spot (fee = 1)
- In composition $\{T_A, T_C\}$: $\texttt{path}$ is NOT a blind spot (fee = 0)

$T_A$'s schema is identical in both cases. Only the composition partner differs.

---

## Theorem 2: Local Equivalence, Global Divergence

**Statement.** There exist pairs of compositions $(G_1, G_2)$ such that:
1. Every tool $T_i \in G_1 \cap G_2$ has identical local schema in both compositions
2. The coherence fees differ: $\text{fee}(G_1) \neq \text{fee}(G_2)$
3. The blind spot sets differ: $\text{BS}(G_1) \neq \text{BS}(G_2)$

**Proof.** Take:
- $G_1 = \{\text{file\_reader}, \text{data\_loader}\}$: shared path_convention → fee = 1
- $G_2 = \{\text{file\_reader}, \text{event\_logger}\}$: no shared dimension → fee = 0

file_reader's schema is locally identical in both. The global structure (which partner) determines the fee. $\square$

**Corollary.** No algorithm that inspects only the schema of a single tool can determine whether a field is a blind spot. Hiddenness identification requires access to the composition graph.

---

## Theorem 3: Diagnostic Sufficiency

**Statement.** Let $G$ be a composition with coherence fee $k = \text{fee}(G)$. Once the hidden set $H^*$ is externally identified (oracle access), the minimum disclosure set has size exactly $k$, and specification is algebraically trivial.

**Proof sketch.** The minimum disclosure set is the basis of the quotient matroid $M/O$, where $M$ is the cycle matroid of the coboundary and $O$ is the observable subspace. By matroid theory, all bases have size $k = \text{rank}(\delta_{\text{full}}) - \text{rank}(\delta_{\text{obs}})$. Once the basis fields are known, their conventions can be checked by direct inspection of the API documentation (or by a single test call). $\square$

**Empirical support.** Oracle-assisted specification experiments show identification rate ≈ fee (trivial selection from disclosed set).

---

## Empirical Thesis: Models Lack Stable Access to the Non-Local Predicate

**Claim (not a theorem — empirical).** Current frontier LLMs do not have a robust internal representation of compositional hiddenness. Instead, they rely on prompt-conditioned heuristics.

**Evidence:**
1. **Vocabulary phenomenon** (N=103): Identification clusters on a small set of lexically canonical field names. OR = 34.5, p < 2.4×10⁻⁶.
2. **Lexical intervention** (N=12): Renaming a field from "direction" to "path" increases identification from 0% to 58% (p = 0.008), holding composition structure fixed.
3. **Cross-model divergence**: On identical compositions, Claude identifies "direction" at 0%, GPT-4o at 88%. Identification is model-specific, not structure-specific.
4. **Prompt contingency**: The lexical intervention effect appears under the structured two-server prompt but disappears under a flat prompt. The effect is task-frame contingent.
5. **Synthetic ecology** (pending): On compositions where ground truth varies with partner, models [give static answers / vary correctly / ???].

---

## The Paper's Logical Arc

```
Theorem 1: Hiddenness is non-local.
  ↓
Theorem 2: Local equivalence, global divergence (constructive).
  ↓
Empirical: Models use lexical proxies instead of structural inference.
  ↓
The proxy behavior is prompt-conditioned (not a stable competence).
  ↓
Theorem 3: Once the non-local object is externally supplied, the problem
           collapses to trivial specification.
  ↓
Construction: Bulla computes the non-local object algebraically.
```

## Formal Definitions

**Composition.** A tuple $G = (\mathcal{T}, E, \delta)$ where $\mathcal{T}$ is a set of tools, $E$ is a set of edges (shared convention dimensions), and $\delta: C^0 \to C^1$ is the coboundary operator.

**Observable projection.** $\delta_{\text{obs}}$ is $\delta$ restricted to observable columns. $\delta_{\text{full}}$ uses all columns (including hidden).

**Coherence fee.** $\text{fee}(G) = \text{rank}(\delta_{\text{full}}) - \text{rank}(\delta_{\text{obs}}) = h^1_{\text{obs}} - h^1_{\text{full}}$, where $h^1 = |E| - \text{rank}(\delta)$ counts independent cycles.

**Blind spot.** An edge $e = (T_i, T_j)$ on dimension $d$ is a blind spot if at least one endpoint field is hidden. The blind spot fields are the hidden endpoints.

**Witness matroid.** The matroid $M$ whose independent sets are the linearly independent subsets of hidden columns in $\delta_{\text{full}}$. The minimum disclosure set is a basis of $M/O$.
