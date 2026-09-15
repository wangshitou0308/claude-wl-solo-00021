"""
数据模型 —— 全部数据与扫描件只存本机 SQLite / MEDIA_ROOT。

核心概念：
* SourceDocument  一份纸质材料（保单 / 续期通知 / 批单）的扫描件与文件信息
* ClauseVersion   材料中出现的某险种条款的一个"版本"（条款文本承载于段落）
* ClauseParagraph 条款版本下的段落；局部替换按段落生效，未涉及段落保留
* Relation        两个条款版本之间的替换 / 增补 / 撤销关系（沿革图的边）
* EvidenceRegion  在扫描图上框选的依据区域，可挂在版本、关系或其日期字段上
* AuditLog        录入 / 修改 / 删除 / 锁定的留痕，支撑"撤销误录"（后进先出）
* QueryConclusion 指定日期核对结论的缓存，关系修改后仅其下游结论过期
"""

from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)


class SourceDocument(TimeStamped):
    """一份纸质材料：保单、续期通知或批单。"""

    class DocKind(models.TextChoices):
        POLICY = "policy", "保单"
        RENEWAL = "renewal", "续期通知"
        ENDORSEMENT = "endorsement", "批单"
        OTHER = "other", "其他材料"

    file_no = models.CharField("文件编号", max_length=64, unique=True)
    title = models.CharField("材料名称", max_length=200, blank=True)
    kind = models.CharField("材料类型", max_length=16, choices=DocKind.choices, default=DocKind.OTHER)
    scan = models.ImageField("扫描图", upload_to="scans/%Y/%m/", blank=True, null=True)

    # 签发日期看不清时保留为范围：issued_earliest ~ issued_latest
    issued_earliest = models.DateField("签发日期（最早可能）", blank=True, null=True)
    issued_latest = models.DateField("签发日期（最晚可能）", blank=True, null=True)
    # 生效区间，open 端用 null 表示（生效至今 / 起始日不明）
    effective_start_earliest = models.DateField("生效起（最早可能）", blank=True, null=True)
    effective_start_latest = models.DateField("生效起（最晚可能）", blank=True, null=True)
    effective_end_earliest = models.DateField("生效止（最早可能）", blank=True, null=True)
    effective_end_latest = models.DateField("生效止（最晚可能）", blank=True, null=True)

    note = models.TextField("备注", blank=True)
    deleted = models.BooleanField("已删除（误录撤销）", default=False, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "材料"
        verbose_name_plural = verbose_name
        ordering = ["file_no"]

    def __str__(self):
        return f"{self.file_no} {self.title}".strip()


class ClauseVersion(TimeStamped):
    """某险种/条款在一份材料中出现的一个版本。"""

    clause_name = models.CharField("险种或条款名称", max_length=200, db_index=True)
    version_label = models.CharField("版本标识", max_length=120, blank=True,
                                     help_text="如：1999版、批单改后版，可留空")
    document = models.ForeignKey(SourceDocument, verbose_name="来源材料",
                                 on_delete=models.PROTECT, related_name="clause_versions")
    note = models.TextField("备注", blank=True)
    deleted = models.BooleanField("已删除（误录撤销）", default=False, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "条款版本"
        verbose_name_plural = verbose_name
        ordering = ["clause_name", "id"]

    def __str__(self):
        return f"{self.clause_name}（{self.version_label or self.document.file_no}）"


class ClauseParagraph(models.Model):
    """
    条款版本下的段落。局部替换按 paragraph_no 对齐；
    未被替换关系涉及的段落始终沿用旧文 —— 系统绝不静默抹去。
    段落同样软删除，以便"撤销误录"完整恢复（包括关系上的段落引用）。
    """

    version = models.ForeignKey(ClauseVersion, verbose_name="所属版本",
                                on_delete=models.CASCADE, related_name="paragraphs")
    paragraph_no = models.CharField("段落号", max_length=40,
                                    help_text="如：第三条、3.2、责任免除第2项")
    title = models.CharField("段落标题", max_length=200, blank=True)
    text = models.TextField("段落文本", blank=True)
    order = models.PositiveIntegerField("顺序", default=0)
    deleted = models.BooleanField("已删除（误录撤销）", default=False, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "条款段落"
        verbose_name_plural = verbose_name
        ordering = ["version_id", "order", "id"]
        # 同号校验放在业务层（软删条目不占用"名额"，撤销删除时可完整恢复）
        indexes = [models.Index(fields=["version", "deleted"])]

    def __str__(self):
        return f"{self.version_id}/{self.paragraph_no}"


class Relation(TimeStamped):
    """
    两个条款版本之间的沿革关系（沿革图的边）。

    replace  全文替换：新版本覆盖旧版本全部段落
    partial  局部替换：只覆盖 affected_paragraphs 中的段落，其余段落沿用
    supplement 增补：新版本作为补充条款与旧版本并行有效
    revoke   撤销：撤销目标版本（整张批单）或某条关系（撤销误录的修改）
                   撤销只"废止"，绝不自动恢复被撤销内容所替换的旧文。
    """

    class Kind(models.TextChoices):
        REPLACE = "replace", "替换（全文）"
        PARTIAL = "partial", "局部替换"
        SUPPLEMENT = "supplement", "增补"
        REVOKE_VERSION = "revoke_version", "撤销条款版本"
        REVOKE_RELATION = "revoke_relation", "撤销一条修改"

    kind = models.CharField("关系类型", max_length=20, choices=Kind.choices)
    document = models.ForeignKey(SourceDocument, verbose_name="依据材料（批单等）",
                                 on_delete=models.PROTECT, related_name="relations")
    from_version = models.ForeignKey(ClauseVersion, verbose_name="新版本/来源版本",
                                     on_delete=models.PROTECT, related_name="outgoing_relations",
                                     blank=True, null=True,
                                     help_text="撤销关系可只填目标，不填来源")
    to_version = models.ForeignKey(ClauseVersion, verbose_name="被修改的旧版本",
                                   on_delete=models.PROTECT, related_name="incoming_relations",
                                   blank=True, null=True)
    target_relation = models.ForeignKey("self", verbose_name="被撤销的关系",
                                        on_delete=models.PROTECT,
                                        related_name="revoked_by",
                                        blank=True, null=True,
                                        help_text="仅「撤销一条修改」使用")
    affected_paragraphs = models.ManyToManyField(
        ClauseParagraph, verbose_name="涉及段落（局部替换）",
        related_name="relations", blank=True)
    description = models.CharField("修改说明", max_length=300, blank=True)

    # 关系本身的发生/生效日期看不清时同样保留为范围
    date_earliest = models.DateField("关系日期（最早可能）", blank=True, null=True)
    date_latest = models.DateField("关系日期（最晚可能）", blank=True, null=True)
    eff_start_earliest = models.DateField("生效起（最早可能）", blank=True, null=True)
    eff_start_latest = models.DateField("生效起（最晚可能）", blank=True, null=True)
    eff_end_earliest = models.DateField("生效止（最早可能）", blank=True, null=True)
    eff_end_latest = models.DateField("生效止（最晚可能）", blank=True, null=True)

    locked = models.BooleanField("已确认锁定", default=False,
                                 help_text="锁定后不可修改/删除，须先解锁")
    deleted = models.BooleanField("已删除（误录撤销）", default=False, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "沿革关系"
        verbose_name_plural = verbose_name
        ordering = ["date_earliest", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk}"


class EvidenceRegion(TimeStamped):
    """
    扫描图上框选的依据区域（图像自身坐标的归一化 0~1 矩形）。

    target 字符串说明依据挂在何处：
      document:<id>[:field]                  材料本身或其某个日期字段
      version:<id>[:paragraph:<pid>][:field] 条款版本 / 段落 / 版本日期字段
      relation:<id>[:field]                  关系或其日期字段
    点击结论逐级回溯时，按这些坐标回到原图对应位置。
    """

    document = models.ForeignKey(SourceDocument, verbose_name="所在扫描件",
                                 on_delete=models.PROTECT, related_name="regions")
    target = models.CharField("挂载目标", max_length=120, db_index=True)
    x = models.FloatField("左（0~1）")
    y = models.FloatField("上（0~1）")
    w = models.FloatField("宽（0~1）")
    h = models.FloatField("高（0~1）")
    label = models.CharField("框选说明", max_length=200, blank=True)

    class Meta:
        verbose_name = "依据框选"
        verbose_name_plural = verbose_name
        ordering = ["id"]


class AuditLog(models.Model):
    """
    操作留痕 + "撤销误录"的后进先出栈。

    仅记录对材料 / 版本 / 段落 / 关系 / 框选的增改删；
    每次撤销弹出栈顶一条未撤销记录并执行其逆操作，撤销本身不入栈。
    """

    class Action(models.TextChoices):
        CREATE = "create", "录入"
        UPDATE = "update", "修改"
        DELETE = "delete", "删除"
        LOCK = "lock", "锁定"
        UNLOCK = "unlock", "解锁"

    action = models.CharField(max_length=10, choices=Action.choices)
    entity_type = models.CharField("实体类型", max_length=20)
    entity_id = models.BigIntegerField("实体ID")
    summary = models.CharField("摘要", max_length=300)
    snapshot = models.JSONField("操作前快照（用于逆操作）", blank=True, null=True)
    undone = models.BooleanField("已撤销", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "操作留痕"
        verbose_name_plural = verbose_name
        ordering = ["-id"]


class QueryConclusion(TimeStamped):
    """
    指定日期核对结论缓存。

    payload 中保存生成该结论所依据的每个关系的 updated_at；
    仅当依赖关系（及其沿"目标版本"向下游传播）被修改时才标记过期并重新生成。
    """

    query_date = models.DateField("指定日期", db_index=True)
    clause_name = models.CharField("条款名称", max_length=200, blank=True,
                                   help_text="空白表示该日期的全量核对")
    payload = models.JSONField("结论内容")
    dependency_hash = models.CharField("依赖指纹", max_length=64)
    stale = models.BooleanField("已过期（需重新生成）", default=False, db_index=True)
    stale_reason = models.CharField("过期原因", max_length=300, blank=True)

    class Meta:
        verbose_name = "核对结论缓存"
        verbose_name_plural = verbose_name
        ordering = ["-query_date", "-id"]
        indexes = [models.Index(fields=["query_date", "clause_name", "stale"])]
