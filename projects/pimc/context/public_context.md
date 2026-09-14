# PIMC 公共上下文：研究知识与工程经验

维护日期：2026-09-13。本文是 PIMC 项目的固定知识正文；每次 Idea 研究和 Reviewer 评审必须加载同一份运行快照。当前任务的目标、约束和新证据在此基础上补充。本文件记录领域知识、当前代码快照和有来源的历史经验；模型新提出的猜想不能自动变成已验证事实。

## 1. PIMC 的物理问题和研究对象

PIMC 在本项目中指无源互调干扰抵消（Passive Intermodulation Cancellation）。FDD 多通道无线发射信号经无源器件和链路非线性产生互调分量，可能落入接收频带。本项目利用已知发射参考 TX 估计接收端 PIM 波形，再从 RX 中减去估计值。它不是路径积分蒙特卡洛，也不是只输出一个性能分数的分类器。

以 t 为采样时刻、c 为通道，X 为发射参考，Y 为接收观测：

$$\widehat{PIM}_\theta(t,c)=f_\theta(X)(t,c),\qquad E(t,c)=Y(t,c)-\widehat{PIM}_\theta(t,c).$$

RX 可能包含 PIM、噪声及其他分量，不能把 RX 全部称作真实无噪声 PIM 标签。NF 是噪声参考，不是模型应该直接拟合的目标。目标通常是降低指定频带内的残差相对噪声底，同时满足复杂度、资源、时延和因果性约束；具体优先级由当前任务决定。

静态场景研究固定采集条件下的非线性和记忆映射。动态场景还包含波束、流数、链路状态切换，研究共享表示、输入驱动路由、有限状态记忆和在线适应。不能把一个静态样例推广成所有动态场景。

## 2. 真实代码、配置和数据入口

- 研究代码根目录：`/Users/harry/Documents/20_paper/code`。
- MARS 仓绑定：`projects/pimc/repo_link.yaml`；真实源码以绑定工作区为准。
- 静态训练：`train_static.py --cfg configs/static.yaml`，叠加 `configs/base.yaml`。
- 模型：`libs/model.py`；模型入口/工厂：`build_pimc_model`。
- 数据：`libs/data.py`；训练：`libs/engine.py`、`train_static.py`；指标：`libs/metrics.py`。
- 动态方法涉及 `libs/model_atk_moe.py`、`libs/model_pc_bre.py`、动态评估脚本和各自配置；研究哪个模型就核对哪个实现，不用相似名字替代。
- 本文包含核心代码片段用于理解；修改设计前仍需核对当前源文件、配置、接口和版本。

当前 `configs/static.yaml` 的样例使用 `.pth` 字典：x=TX、y=RX、nf=噪声参考，配置也标记 y_f 为滤波 RX；数据格式能力及键映射以加载器为准。历史默认样例路径为 `/Users/harry/Documents/20_paper/data/static/pim_16t_221110_38dBm_fr4_rnd32_1.pth`。不能仅凭本文的路径断言当前任务已选中该文件或该路径仍可用。

磁盘常见布局是 `(C,T)`，StaticPIMC 前向是 `(T,C)` 复数；入口负责转换。16 通道、196608 个采样是已有样例的描述，不是领域常量。必须从当前数据或元信息核对通道数、采样率、样本划分和有效频带。

## 3. StaticPIMC 的结构和公式

当前静态基线是五层映射：

$$f_\theta(X)=L_5\left(L_4\left(L_3\left(L_2\left(L_1(X)\right)\right)\right)\right).$$

L1/L5 为逐通道复卷积（时域记忆）；L2/L4 为跨通道复卷积（通道组合与记忆）；L3 为逐通道幅度相关非线性。复卷积可写成 $z_o(t)=\sum_{i,k}w_{oik}x_i(t-dk)+b_o$；真实 padding、bias 和分组方式必须按具体实现核对，不能只凭这条记号推算边界。

当前静态配置快照：L1/L5 kernel=21、dilation=2、groups=16；L2/L4 kernel=17、dilation=2、groups=1；LUT 节点 K=16、r_max=0.4、初始增益 0.001。均是可变配置，不写成永久限制。单位初始化卷积和接近零的 LUT 初值需要一起理解；接近零输出并不自动意味着所有参数没有梯度。

原始 complex_gain LUT 定义：

$$r_c(t)=\operatorname{clip}(|x_c(t)|/r_{max},0,1),\quad q_k=k/(K-1),\quad h=1/(K-1),$$
$$b_k(r)=\max(1-|r-q_k|/h,0),\quad g_c(r)=\sum_{k=0}^{K-1}w_{ck}b_k(r),\quad L_3(x_c)=x_c g_c(r_c).$$

$w$ 是复数，因此增益可影响幅度和相位。基础 LUT 有 C×K 个复参数，即 2CK 个实标量。实际总参数还包括卷积核、bias、路由与其他可训练量。复数参数统一折算成两个实数，不能混用参数口径。

`lut_type` 当前支持原始 `complex_gain`、实权重幅度增益 `magnitude`、`mlp_gain` 等由工厂确认的分支。重要代码差异：本次核对时 `MagnitudeLUT1D.forward` 的最终返回已经是 `y.squeeze(-1).T * x`，其结果保留原复输入因子；旧注释仍写输出为实数。设计必须依据当前执行代码，不得继承过期注释或历史描述。原始 LUT 有 r_max 归一化和截断，magnitude 这段实现没有同样的显式归一化/截断，不能仅按名字认为两者等价。

## 4. 指标与损失：必须分清口径

`libs/metrics.py` 接收每通道带内功率的 dB 值。令 p_c=Pe_rx,c−Pe_nf,c，r_c=Pe_err,c−Pe_nf,c：

$$PIM=10\log_{10}\left({1\over C}\sum_c10^{p_c/10}\right),\quad RES=10\log_{10}\left({1\over C}\sum_c10^{r_c/10}\right).$$

跨通道先在线性域汇总，不能直接对通道 dB 求平均冒充该指标。最差通道值由相应线性比值最大值计算。当前代码将汇总 RES 小于 0.1 dB 的情况截到 0.1 dB 后计算：

$$APE=10\log_{10}(10^{PIM/10}-1)-10\log_{10}(10^{RES/10}-1).$$

因此 APE 是扣除噪声参考后的抵消改善量，单位 dB，不是角度或相位误差，也一般不等于简单的 PIM−RES。贴近噪声底时需要理解数值截断，不能把它当实测物理下界。

在 MARS paper_static 适配器中，论文指标名为 paper_RES_db（越低越好）、paper_APE_db（越高越好）；旧兼容字段 RES=-paper_APE_db、loss=10**(-paper_APE_db/10)。这里的兼容 loss 不能冒充训练优化器实际使用的 loss。源码也有 distance_to_noise_floor 辅助函数，不能不检查调用语义就把相对噪声底的 RES 再与绝对 dB 电平混算。

当前静态配置默认 loss.type=l2，配置注释称默认 RMSE，另有 huber、spectral_floor 等可选路径；精确训练目标、滤波、归一化和 batch 规则必须以 `libs/engine.py` 实际分支为准。比较候选时训练 loss 和最终目标频带指标都要写清。

## 5. 频带、训练和数据使用经验

当前静态样例 fs=245.76 MHz，PIM 统计频带为 [-29.74,-24.74] MHz，frq_shift=0；滤波配置默认 lowpass、cutoff=50 MHz、fir_taps=255。它们只属于该配置，不能覆盖新数据的元信息。改损失频带、采样、通道尺度、训练控制时，明确改变的是哪条实际执行路径。

当前配置 Etotal=20、Epoch=10，合成 200 epochs；200 个优化更新不等于 200 epochs。scheduler.step 的频率、学习率衰减、每 epoch batch 数需要核查入口。当前静态默认 lr_init=0.0004、step_size=50，是来源快照而非永久最优设置。

保留训练/验证/测试边界。路由器、归一化、投影、模型选择只使用协议允许的信息。动态场景应区分已见状态、未见波束、未见组合和数据包之间的迁移；均值、最差通道、最差窗口和切换瞬态回答不同问题。

## 6. 动态研究路线与关键机制

历史研究包含 HyperLUTPIMC、低秩记忆、共享主干/残差专家、ATK-MoE、PC-BRE 相关设计、物理软权重和 CoherenceScan-D。名称相近不代表同一结构，下面按来源分别记录。

2026-07-16 的 `docs/aaai27_final_method_description_zh.md` 描述一种20分支路线：共享 L1 → 低秩非线性分支 → 复数加权和 → 共享 L5。分支可概括为：

$$z_b=\sum_r Conv_r(HA_{b,r}^{reduce}),\quad v_b=z_b LUT_b(|z_b|),\quad F_b=\sum_r A_{b,r}^{expand}Conv_r(v_b),$$
$$\widehat{PIM}=L_5\left(\sum_{b\in S(X)}g_bF_b\right).$$

这些分支是共同学习的非线性基，不是每个 case 配一个完整 StaticPIMC。该历史版本使用独立复数门控，不能未经推导就换成和为1的 softmax；其他路由分支可能使用概率权重，必须分别说明。

文档中曾使用 TX-only 的78维描述符（通道功率、空间FFT功率、协方差特征值、相邻通道相干性），低秩 scorer 负责选分支，K-head 负责执行多少分支；该版本 K∈{18,19,20}，不能宣称已经达到极端稀疏。6维上下文投影、40槽记忆、Top-3检索和有界 RLS 都是该版本的实现选择，不是全项目常量。

严格因果关系：第t批次的 RX/ERR 更新只能影响第t+1批次及以后使用的状态。当前窗口内来自 TX 的信息和整段 case 的未来信息不能混用。case-ID oracle 只作上界，不等于可部署路由器。

CoherenceScan-D 的 `docs/coherence_scan_router_design.md` 是另一条物理软权重路线：相邻通道配对，窗口内 RMS 归一化，以中心频率导向向量计算空间响应，再池化到任意 D 个权重。文档的计算量优势首先针对物理权重生成器；不能直接外推为整网 FLOPs、端到端吞吐或 PIM 性能收益。它可以接轻量可学习校准器，但应与上面的独立复门控版本分开描述。

## 7. 既往研究、负结果和失效实验

以下是已存在研究文档的历史记录，本次知识整理没有重跑这些实验。精确性能比较需打开相应结果、配置和checkpoint核查。

| 记录 | 结论与适用边界 | 来源 |
|---|---|---|
| low-shared-core screen，2026-07-16 | 直接把已训练的共享分支缩到2或4，多个候选落入明显劣势区域；这是否定该截断方法，不是否定所有自适应路由。 | docs/aaai27_low_shared_core_negative_screen_20260716.md |
| 错用其他数据包的上下文router | 部分旧run的package 2/3/4不能用于绝对性能或汇总论文表；应使用各包独立训练的router资产。 | docs/invalid_dynamic_runs_20260715.md |
| rank不匹配 | rank-4 checkpoint曾被旧启动器按rank-3重建并兼容加载，不能视为有效rank-4结果。 | 同上 |
| 小shared-core的K-anchor实现不符 | 声明分支调度与实际执行容量不符，不能把该结果列入Pareto表。 | docs/invalid_saliency_dynamic_primary_v1.md |
| 整case路由的未来信息 | 读取整段case的路由结果不能与短因果窗口公平比较。 | docs/invalid_dynamic_runs_20260715.md |
| 低参数/低复杂度方向 | 参数减少与性能保持必须分别验收；单个压缩比例达成不能证明可替代基线。具体历史压缩结果需核对各run。 | configs/static_compression80*.yaml及对应runs |

## 8. 人类积累的工程与研究经验

1. 保留原始 baseline。LUT、MLP、低秩、路由等变体通过显式开关和独立记录比较，不覆盖原参考实现。
2. 新方案应具体到当前代码能消费的改动，不能只提出无人读取的配置项或抽象口号。
3. 新改进优先隔离一个机制；涉及参数量、数据量、训练预算差异时如实说明，不补无效参数以凑相等数量。
4. 不把小规模接口示例、CI通过、模型列表可访问或 TensorBoard 可见当作研究成功。
5. 长训练要保留可检查的进度、配置、曲线和checkpoint。JSON/CSV与来源记录用于追溯，面板展示不能替代原始结果。
6. 没有效果的实验和实现无效的实验分开保存。前者可以提供方法判断，后者不能支持性能结论。
7. 提案保留原方案、评审意见、修订内容和失败原因。合理迁移可以作为待验证假设；模型评审不是实际收益证明。
8. 文献检索服务于信息缺口。不要按固定篇数停止，采用的方法需要读完整。必须说清选择/排除理由以及思路如何进入当前方案。
9. 不自动把模型新猜想沉淀成已确认知识。更新本文时保留“代码事实/实验结论/经验判断/待验证假设”的区别。

## 9. 保护边界和本次任务的关系

项目 AGENTS.md 中历史冻结项包括 Paper_Total_0327、动态接口 forward(x, stream_label)、baseline/** 和 production_interface/**。这些属于保护规则，不意味着当前静态入口一定使用 Paper_Total_0327。当前 StaticPIMC 的接口是 forward(x)。运行哪个模型，应读取其实际工厂、配置和方法体。

用户本次明确指令决定具体研究目标；本文默认配置、历史最佳、历史候选不能覆盖新的数据或目标。遇到本文与当前代码、数据或用户更正冲突时，指出差异并核实，不自行猜测。所有性能收益、硬件收益和新颖性保证必须有相应证据；Idea阶段可以清楚提出待验证的方案。

## 10. 核心代码快照

以下片段来自本次读取的实际研究代码，供理解模型和指标使用。完整实现仍在原研究仓。

### 原始 complex_gain LUT

```python
class LUT1D(nn.Module):
    """Per-channel amplitude-dependent complex gain via a piecewise-linear
    (linear-spline) look-up table with ``n_spline`` knots.

        gain(|x|) = sum_k  w[c, k] * tri_k(|x| / r_max)
        out       = x * gain

    ``tri_k`` are unit triangular (hat) basis functions centred on uniformly
    spaced knots in [0, 1] — i.e. classic LUT linear interpolation expressed as a
    differentiable basis sum. Identity-initialised (w = 1 -> gain = 1 -> pass
    through), matching the identity-init convs around it.
    """
    def __init__(self, channels, n_spline=16, r_max=1.0, init_gain=1.0):
        super().__init__()
        self.channels = channels
        self.n_spline = n_spline
        self.r_max = r_max
        self.register_buffer('knots', torch.linspace(0.0, 1.0, n_spline))
        self.w = nn.Parameter(torch.ones(channels, n_spline, dtype=torch.cfloat) * init_gain)

    def forward(self, x):
        # x: [T, C] complex
        r = (x.abs() / self.r_max).clamp(0.0, 1.0)              # [T, C]
        h = 1.0 / (self.n_spline - 1)
        dist = (r.unsqueeze(-1) - self.knots) / h               # [T, C, K]
        basis = (1.0 - dist.abs()).clamp(min=0.0)               # [T, C, K]
        gain = torch.einsum('tck,ck->tc', basis.to(self.w.dtype), self.w)   # [T, C]
        return x * gain
```

### StaticPIMC 前向

```python
def forward(self, x):
        x = x.cfloat()
        x = self.L1(x)
        x = self.L2(x)
        x = self.L3(x)
        if not x.is_complex():
            x = x.cfloat()
        x = self.L4(x)
        x = self.L5(x)
        return x
```

### 论文指标计算

```python
def get_performance(Pe_rx, Pe_nf, Pe_err, chnl, printf=False):
    pim = np.zeros(chnl)
    res = np.zeros(chnl)
    ape = np.zeros(chnl)

    for i in range(chnl):
        pim[i] = Pe_rx[i] - Pe_nf[i]
        res[i] = Pe_err[i] - Pe_nf[i]

    PIM = 10 * np.log10(np.mean(np.power(10, pim / 10)))
    RES = 10 * np.log10(np.mean(np.power(10, res / 10)))

    PIM_MAX = 10 * np.log10(np.max(np.power(10, pim / 10)))
    RES_MAX = 10 * np.log10(np.max(np.power(10, res / 10)))

    if RES < 0.1:
        RES = 0.1

    APE = 10 * np.log10(10 ** (PIM / 10) - 1) - 10 * np.log10(10 ** (RES / 10) - 1)

    if printf:
        print('-----------------------------')
        print('PIM_Mean: %.2f' % PIM)
        print('Res_Mean: %.2f' % RES)
        print('PIM_Max: %.2f' % PIM_MAX)
        print('Res_Max: %.2f' % RES_MAX)

    return PIM, RES, APE, PIM_MAX, RES_MAX
```

## 11. 版本与来源索引

研究仓 HEAD：`47aa770389a234bcdb7a7585cb099194b35e7322`。文件哈希记录本次实际读取版本，工作区内容可能不同于HEAD；不把HEAD单独当作完整来源。

- [libs/model.py](/Users/harry/Documents/20_paper/code/libs/model.py)，SHA-256 `721cc7dda963984a2e37755381ae8e17562d75113a35677f3100a60f7cf433e7`。
- [libs/metrics.py](/Users/harry/Documents/20_paper/code/libs/metrics.py)，SHA-256 `38715cc432c52845e939191e2fa3eb20d8ecda2bab41e2112b16b43c3cd38d84`。
- [configs/static.yaml](/Users/harry/Documents/20_paper/code/configs/static.yaml)，SHA-256 `6ce90a4639fbe9eec6376ba4ecb508f8a3c4b0eab8c8f3423b72f03ea60173d7`。
- [docs/aaai27_final_method_description_zh.md](/Users/harry/Documents/20_paper/code/docs/aaai27_final_method_description_zh.md)，SHA-256 `89cb783e2c8f2f9ad2f59a9998db3597e90bce671e235d060bc6e3760937eb7c`。
- [docs/coherence_scan_router_design.md](/Users/harry/Documents/20_paper/code/docs/coherence_scan_router_design.md)，SHA-256 `a6471f55d58b9e3b89d6576daff49b2fffa33c7b3dcfacc6dab621ff64d79818`。
- [docs/aaai27_low_shared_core_negative_screen_20260716.md](/Users/harry/Documents/20_paper/code/docs/aaai27_low_shared_core_negative_screen_20260716.md)，SHA-256 `7f1931f6d54998c1f833f7eb36b3b07023a3bac601aeebc918e4277686874a51`。
- [docs/invalid_dynamic_runs_20260715.md](/Users/harry/Documents/20_paper/code/docs/invalid_dynamic_runs_20260715.md)，SHA-256 `e0ae31267b6a1405a85b504d6554aa52d325d5e719a71004a242d0c8b9b6dc98`。
