---
schema: research_report.v1
project: pimc
human_summary: 找到第三篇独立论文：RBF近似（arXiv:1806.07705），其网格无关的M个中心权重+多项式再生公式可直接迁移为256参数2D标量LUT的替代参数化，中心可非均匀布置以匹配局部高曲率。
gaps:
- id: gap_third_pub
  question: Find a THIRD distinct retrieved publication (not Conv2Warp arXiv 1908.06194
    or Instant NGP arXiv 2201.05989) on interpolation/parameterization schemes for
    2D real-valued scalar maps / learnable LUTs that could improve expressiveness
    over bilinear interpolation under a fixed 256-scalar-parameter budget, with concrete
    formula, parameter count, differentiability, boundary handling, and training properties.
selection_principles:
- Must be a distinct publication different from Conv2Warp (1908.06194) and Instant
  NGP (2201.05989).
- Must present a concrete interpolation/parameterization formula with explicit parameter
  count and differentiability, applicable to a 2D real scalar map under a 256-parameter
  budget.
- Must address boundary handling and training/optimization properties (e.g., how weights
  are solved) relevant to a learnable LUT.
- Method pages must be actually read from the PDF with a visible quote and read receipt.
sources:
- source_id: rbf_majdisova_skala
  url: https://arxiv.org/abs/1806.07705
  title: 'Radial basis function approximations: comparison and applications'
  decision: use
  selection_reason: Selected as the third distinct publication. It gives a concrete
    grid-free RBF parameterization f(x)=Σ c_j φ(‖x−ξ_j‖) with M trainable weights
    plus optional polynomial reproduction, explicitly supports non-uniform reference-point
    placement (relevant to local high curvature), and states the exact variable count
    (M+3 in E²) and least-squares solution, all directly transferable to a 256-parameter
    2D scalar LUT. Rejected the Chebyshev/spectral hits (0706.2286, 1312.7845, 2011.10395,
    2108.08481) as too PDE/operator-oriented or lacking a concrete 2D scalar-map parameter
    budget; rejected PetRBF (0909.5413) and RBF-ENO (1602.00183) as implementation/algorithmic
    rather than parameterization-focused.
  gap_ids:
  - gap_third_pub
insights:
- id: insight_rbf_formula
  source_id: rbf_majdisova_skala
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/a0ad8f153df94041beca59b4196031e7.read.json
  document_sha256: 24017e99dbaab4b282da8e7b421e26e628b1efb20b21f84a994d3fd582d87b27
  page: 4
  quote: where the approximating functionf(x) is represented as a sum ofM RBFs, each
    associated with a different reference pointξj, and weighted by an appropriate
    coefficientcj.
  paper_finding: The paper defines a grid-free RBF approximant as a weighted sum of
    M radial basis functions centered at reference points ξ_j, with weights c_j as
    the only unknowns; the overdetermined system h_i = f(x_i) is solved by least squares
    (A^T A c = A^T h) or SVD.
  transfer_idea: For a 256-parameter 2D scalar LUT, replace the fixed 16×16 bilinear
    grid with M RBF centers whose weights c_j are the trainable parameters; centers
    ξ_j can be placed non-uniformly to concentrate resolution where curvature is high,
    and the map is differentiable in both c_j and ξ_j (if centers are also trained),
    giving a grid-free alternative to bilinear interpolation.
  limitations:
  - Paper is about scattered-data approximation, not a trained neural/LUT layer; it
    does not discuss gradient-based training or backpropagation of the weights.
  - RBF systems are typically ill-conditioned (dense matrix) and sensitive to the
    shape parameter α of the chosen kernel, which must be tuned.
  - Boundary handling on the fixed domain [-1,1]^2 is not addressed; RBFs are global
    so values outside the center hull need explicit treatment.
  - The paper's M≪N reduction framing assumes many data points N; under a strict 256-parameter
    budget the number of usable centers is limited and the paper gives no guidance
    on that regime.
- id: insight_rbf_nonuniform_centers
  source_id: rbf_majdisova_skala
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/a0ad8f153df94041beca59b4196031e7.read.json
  document_sha256: 24017e99dbaab4b282da8e7b421e26e628b1efb20b21f84a994d3fd582d87b27
  page: 4
  quote: These reference points may not necessarily be in a uniform grid. It is appropriate,
    that their placement reflects the given surface as well as possible.
  paper_finding: The paper states that RBF reference points need not lie on a uniform
    grid and that placing them to reflect the underlying surface (e.g., along break
    lines of a terrain) improves approximation quality.
  transfer_idea: 'This directly supports the hypothesis that non-uniform sampling
    can improve expressiveness: under a 256-parameter budget, RBF centers (or a non-uniform
    grid) concentrated in high-curvature regions of the 2D map could beat a fixed
    uniform 16×16 bilinear grid, and center locations could themselves be trained
    as parameters.'
  limitations:
  - The claim is qualitative and demonstrated on terrain/scattered-data examples,
    not on a fixed-budget learnable 2D scalar map; no quantitative rule is given for
    how many centers to place where.
  - Non-uniform placement is a user choice in the paper, not an automatically learned
    quantity, so the paper does not establish that gradient-based center learning
    converges.
- id: insight_rbf_polynomial_reproduction
  source_id: rbf_majdisova_skala
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/a0ad8f153df94041beca59b4196031e7.read.json
  document_sha256: 24017e99dbaab4b282da8e7b421e26e628b1efb20b21f84a994d3fd582d87b27
  page: 5
  quote: Therefore, the RBF approximant (/seven.prop) is usually extended by polynomial
    functionPk(x) of degreek.
  paper_finding: The RBF approximant is extended by a polynomial reproduction term
    P_k(x); with a linear polynomial P_1(x)=a^T x + a_0 in E² the system has N equations
    in (M+3) variables (M RBF weights plus a_x, a_y, a_0).
  transfer_idea: 'This gives an exact parameter budget: with 256 total trainable scalars,
    use M=253 RBF weights plus 3 linear-polynomial coefficients (a_x, a_y, a_0) =
    256, yielding a smooth, differentiable 2D map that exactly reproduces affine trends
    and adds localized RBF corrections — a concrete candidate scheme to compare against
    bilinear interpolation.'
  limitations:
  - The polynomial degree is fixed at linear in the paper's practice; higher-order
    reproduction would consume more of the 256-parameter budget.
  - The paper solves the coefficients by least squares over given data, not by stochastic
    gradient descent, so training dynamics and conditioning under backpropagation
    are not established.
  - Boundary/domain handling for the polynomial term on [-1,1]^2 is not specified
    in the paper.
---

找到第三篇独立论文：RBF近似（arXiv:1806.07705），其网格无关的M个中心权重+多项式再生公式可直接迁移为256参数2D标量LUT的替代参数化，中心可非均匀布置以匹配局部高曲率。