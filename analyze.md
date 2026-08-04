# OAI CSI + iperf 数据处理与信道容量分析任务规范

## 目标

基于现有 OAI 导出的 **CSV 测量文件**（每行对应一条 iperf3 输出）以及对应的 **CSI 信道文件目录**，完成数据清洗、CSI 匹配、信道容量计算、ZF 预编码 + MMSE 接收机分析，并生成新的分析 CSV 文件。

整个流程必须 **不修改原始 CSV 文件**，所有结果输出到新的 CSV 文件。

---

# 数据组织方式

每个 CSV 文件对应一个同名文件夹比如：

```
/home/qiuer/Documents/openairinterface5g/gui/record/gui_log_20260804_212102.csv
```

其中：

* CSV 每一行对应 **1 秒 iperf3 输出**
* 同名文件夹内保存该 CSV 对应的 CSI 文件
* CSI 文件名中包含时间戳
* 每个 CSI 文件对应 CSV 中同一时刻的一条记录

---

# 第一阶段：CSV 数据清洗

## 数据特点
类型a（比如/home/qiuer/Documents/openairinterface5g/gui/record/gui_log_20260804_212102.csv），采集方式为：

* 在同一位置进行连续 **30 秒 iperf3 测速**
* 不同位置之间有 **5 秒以上空闲间隔**
* 因此 CSV 中会包含：

```
缓存数据
空闲数据
真正测速数据
```
类型b，70s连续位置变换并测速，每条都是有效数据（暂无示例csv文件）

## 任务

自动识别所有测速区段。

要求：

* 每个位置保留 **30 条有效数据**
* 去除测速开始前后的缓存/空闲记录
* 最终得到：

```
N 个位置 × 30 条记录
```

即：

```
30 × N 条数据
```

输出：

```
cleaned_measurement.csv
```

并输出清洗日志

---

# 第二阶段：CSI 文件匹配

对于 cleaned_measurement.csv 中保留的每一条记录：

根据时间戳找到同名目录中的 CSI 文件。

要求：

* 一条 CSV 对应一个 CSI 文件
* 时间戳允许小范围误差（如 ±200 ms）
* 若找不到：

```
NaN
```

并记录 warning。

---

# 第三阶段：CSI 信道矩阵解析

假设系统为：

```
4 × 4 MIMO
```

对于每个 CSI 文件：

恢复每个有效子载波的复信道矩阵：

```
H(k)
```

其中：

```
H(k) ∈ C^(4×4)
```

要求：

* 跳过没有信道估计结果的子载波
* 仅对有效子载波参与计算



---

# 第四阶段：有效 SNR

使用该秒对应的 RSRP 构造容量计算 SNR。

定义：

```
EffectiveSNR_dB = RSRP + 100
```

例如：

```
RSRP = -85 dBm

SNR = 15 dB
```

转换为线性：

```
ρ = 10^(SNR/10)
```

所有容量计算均使用该有效 SNR。

---

# 第五阶段：功率归一化

必须先进行 **噪声功率归一化**。

信道矩阵的能量归一至有效snr，认为噪声功率为1

需要验证：

```
||H||_F^2
```

与

```
Σ λ_i
```

的一致性。

其中：

```
λ_i
```

为

```
H H^H
```

的四个特征值。

要求：

验证：

```
||H||_F^2 ≈ λ1 + λ2 + λ3 + λ4
```

误差应在数值精度范围内。

若不满足：

必须报告原因，不得继续使用错误归一化。

---

# 第六阶段：奇异值分析

对每个 CSI：

计算：

```
H H^H
```

得到：

```
λ1
λ2
λ3
λ4
```

要求：

按降序排列。

同时验证：

```
λ1 + λ2 + λ3 + λ4
```

与归一化功率关系。

---

# 第七阶段：SVD 预编码信道容量

对于空间流数：

```
K = 1
K = 2
K = 3
K = 4
```

计算：

```
C_SVD(K)
```

公式：

```
C = Σ log2(1 + ρ/K · λ_i)
```

要求：

* 每个 K 分别计算
* 对所有有效子载波取平均

输出：

```
CapacitySVD_K1
CapacitySVD_K2
CapacitySVD_K3
CapacitySVD_K4
```

---

# 第八阶段：ZF 预编码 + MMSE 接收机

对每个 K：

构造：

```
W_ZF
```

满足：

* ZF 预编码
* 发射功率归一化

得到等效信道：

```
H_eq = H W_ZF
```

然后构造 MMSE 接收机。

计算：

## 每流 SINR

例如：

```
K = 2

SINR1
SINR2
```

对于：

```
K = 4

SINR1
SINR2
SINR3
SINR4
```

---

## ZF + MMSE 容量

计算：

```
CapacityZFMMSE(K)
```

由各流 SINR 得到：

```
C = Σ log2(1 + SINR_i)
```

要求：

对所有有效子载波取平均。

输出：

```
CapacityZFMMSE_K1
CapacityZFMMSE_K2
CapacityZFMMSE_K3
CapacityZFMMSE_K4
```

---

# 第九阶段：数学一致性验证

必须完成以下验证：

## 1. 功率验证

验证：

```
||H||_F^2
```

与

```
Σ λ_i
```

一致。

---

## 2. SVD 容量验证

验证：

```
C_SVD
```

与特征值公式一致。

---

## 3. ZF 功率约束验证

验证：

```
trace(W W^H)
```

满足功率归一化。

---

## 4. SINR 合理性验证

验证：

* SINR 非负
* 随 K 增大变化合理
* 与奇异值趋势一致

---

若验证失败：

必须输出详细报告：

```
row_id
csi_file
expected
actual
relative_error
possible_reason
```

---

# 第十阶段：输出 CSV

生成新的 CSV：

```
channel_analysis.csv
```

**不要修改原始 CSV 文件。**

每一行对应：

```
一个 iperf 样本，k=1/2/3/4
```

表头如下：

```
Timestamp,
PositionID,
SampleIndex,
ThroughputMbps,
RSRP_dBm,
EffectiveSNR_dB,
EffectiveSNR_Linear,

Eigenvalue1,
Eigenvalue2,
Eigenvalue3,
Eigenvalue4,
EigenvalueSum,
FrobeniusNorm2,

CapacitySVD


CapacityZFMMSE


SINR_Stream1,
SINR_Stream2,
SINR_Stream3,
SINR_Stream4,
（没有则为0，比如1流就只有SINR_Stream1不为0）

CSI_File
```

---

# 最终要求

Agent 必须：

1. 先分析数据格式
2. 自动完成 CSV 清洗
3. 自动匹配 CSI
4. 完成信道归一化
5. 验证特征值关系
6. 使用 RSRP+100 作为有效 SNR
7. 计算 SVD 容量
8. 计算 ZF 预编码 + MMSE 接收机容量
9. 计算每流 SINR
10. 生成 `channel_analysis.csv`
11. 输出验证报告，证明功率归一化和容量计算实现正确

**重点：优先保证数学一致性与归一化正确性，而不是先生成结果。若验证失败，应停止并报告原因。**
