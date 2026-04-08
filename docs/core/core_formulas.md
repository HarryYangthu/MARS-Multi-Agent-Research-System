# Core Formulas

> 当前为 Phase-0 占位文档，先定义公式组织方式，不填入复杂推导细节。

## 1. Signal Model（预留）

- 发射信号表示：\(x(t)\)
- 接收观测：\(y(t) = s(t) + i_{pim}(t) + n(t)\)

其中：
- \(s(t)\)：目标有用信号
- \(i_{pim}(t)\)：PIM 干扰项
- \(n(t)\)：噪声项

## 2. PIM Interference Approximation（预留）

可使用多项式/Volterra 类近似表示：
\[
i_{pim}(t) \approx \sum_k a_k \phi_k(x(t))
\]

## 3. Cancellation Objective（预留）

通过构造估计项 \(\hat{i}_{pim}(t)\) 进行抵消：
\[
y_{clean}(t) = y(t) - \hat{i}_{pim}(t)
\]

## 4. Metrics（预留）

- 抵消增益（dB）
- 均方误差（MSE）
- 计算复杂度与运行时开销

## 5. TODO

- 在后续阶段补充与 `codespec/specs/domain-pimc/formulas.md` 的映射关系。
- 补充每个公式对应的实验验证入口与数据需求。
