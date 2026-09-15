"""
五个已报告缺陷的回归测试（HTTP/服务层）。

1. 扫描图上传后接口必须返回 scan 字段，前端才能看图/框选/回溯；
2. PATCH 材料标题不得抹掉未提交的签发/生效日期；
3. 录入/修改段落后执行"撤销误录"必须可恢复，不报 500；
4. 修改较晚批单关系不得使更早日期的核对结论误报过期；
5. 删除已锁定关系返回 409 + 明确提示，而非 500。
"""

import datetime as dt
import io

from django.test import TestCase, Client
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from endorsements.models import (ClauseParagraph, ClauseVersion, QueryConclusion,
                                 Relation, SourceDocument)
from endorsements.services import save_conclusion
from endorsements import serializers

D = dt.date


def png_bytes(color="white"):
    img = Image.new("RGB", (200, 260), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class RegressionTests(TestCase):
    def setUp(self):
        self.c = Client()
        self.policy = SourceDocument.objects.create(
            file_no="P-1", title="旧保单", kind="policy",
            issued_earliest=D(1999, 3, 1), issued_latest=D(1999, 3, 1),
            effective_start_earliest=D(1999, 4, 1),
            effective_start_latest=D(1999, 4, 1))
        self.end2005 = SourceDocument.objects.create(
            file_no="E-2005", title="批单2005", kind="endorsement",
            issued_earliest=D(2005, 5, 1), issued_latest=D(2005, 5, 1),
            effective_start_earliest=D(2005, 6, 1),
            effective_start_latest=D(2005, 6, 1))
        self.end2010 = SourceDocument.objects.create(
            file_no="E-2010", title="批单2010", kind="endorsement",
            issued_earliest=D(2010, 5, 1), issued_latest=D(2010, 5, 1))
        self.v1 = ClauseVersion.objects.create(
            clause_name="意外险", version_label="1999版",
            document=self.policy)
        ClauseParagraph.objects.create(
            version=self.v1, paragraph_no="第一条", text="旧文")
        self.v2 = ClauseVersion.objects.create(
            clause_name="意外险", version_label="2005版",
            document=self.end2005)
        ClauseParagraph.objects.create(
            version=self.v2, paragraph_no="第一条", text="新文")
        self.rel2005 = Relation.objects.create(
            kind="replace", document=self.end2005,
            from_version=self.v2, to_version=self.v1,
            date_earliest=D(2005, 5, 1), date_latest=D(2005, 5, 1))

    # --- 1. 扫描图字段 ---------------------------------------------------- #
    def test_scan_field_present_after_upload(self):
        r = self.c.post("/api/documents/", {
            "file_no": "P-IMG", "kind": "policy",
            "issued_earliest": "2000-01-01", "issued_latest": "2000-01-01",
            "scan": SimpleUploadedFile("scan.png", png_bytes(),
                                       content_type="image/png"),
        }, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        doc = r.json()["document"]
        self.assertIn("scan", doc)
        self.assertTrue(doc["scan"].endswith(".png"))
        # 列表接口同样要有 scan
        items = self.c.get("/api/documents/").json()["items"]
        self.assertTrue(any(d["file_no"] == "P-IMG" and d["scan"]
                            for d in items))

    # --- 2. PATCH 不得抹日期 --------------------------------------------- #
    def test_patch_title_keeps_dates(self):
        r = self.c.patch(f"/api/documents/{self.policy.id}/",
                         {"title": "改了标题"}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.title, "改了标题")
        self.assertEqual(self.policy.issued_earliest, D(1999, 3, 1))
        self.assertEqual(self.policy.issued_latest, D(1999, 3, 1))
        self.assertEqual(self.policy.effective_start_earliest, D(1999, 4, 1))

    def test_patch_relation_keeps_dates(self):
        r = self.c.patch(f"/api/relations/{self.rel2005.id}/",
                         {"description": "只改说明"},
                         content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.rel2005.refresh_from_db()
        self.assertEqual(self.rel2005.date_earliest, D(2005, 5, 1))
        self.assertEqual(self.rel2005.date_latest, D(2005, 5, 1))

    # --- 3. 段落撤销 ------------------------------------------------------ #
    def test_undo_paragraph_create_and_delete(self):
        # 录入段落
        r = self.c.post("/api/paragraphs/", {
            "version_id": self.v1.id, "paragraph_no": "第二条",
            "text": "误录段落"}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        pid = r.json()["paragraph"]["id"]

        # 撤销"录入" → 段落软删，不报错
        r = self.c.post("/api/undo/", {}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        undone = r.json()["undone"]
        self.assertEqual(undone["entity_type"], "paragraph")
        self.assertEqual(undone["action"], "create")
        self.assertFalse(ClauseParagraph.objects.filter(pk=pid).exists())
        self.assertTrue(ClauseParagraph.all_objects.filter(
            pk=pid, deleted=True).exists())

        # 直接验证软删段落的恢复路径（模拟用户在留痕中撤销那次"删除"）：
        # 再删一个段落然后撤销删除，段落应原样回来
        p2 = ClauseParagraph.objects.create(
            version=self.v1, paragraph_no="临时段", text="x")
        r = self.c.delete(f"/api/paragraphs/{p2.id}/")
        self.assertEqual(r.status_code, 200)
        r = self.c.post("/api/undo/", {}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        undone = r.json()["undone"]
        self.assertEqual(undone["action"], "delete")
        self.assertTrue(ClauseParagraph.objects.filter(pk=p2.id).exists())

    def test_undo_deleted_paragraph_restores_relation_link(self):
        p = self.v2.paragraphs.first()
        self.rel2005.kind = "partial"
        self.rel2005.save()
        self.rel2005.affected_paragraphs.add(p)
        r = self.c.delete(f"/api/paragraphs/{p.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rel2005.affected_paragraphs.count(), 0)
        # 撤销删除 → 段落恢复，且自动重新挂回关系
        r = self.c.post("/api/undo/", {}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(ClauseParagraph.objects.filter(pk=p.id).exists())
        self.assertIn(p, self.rel2005.affected_paragraphs.all())

    # --- 4. 下游失效的日期门控 ------------------------------------------- #
    def test_late_relation_change_keeps_early_conclusion_fresh(self):
        # 2003 年的核对结论（早于 2005、2010 批单）
        payload_2003 = serializers.analyze(D(2003, 1, 1))
        save_conclusion(D(2003, 1, 1), payload_2003)
        # 2006 年的核对结论（受 2005 批单影响）
        payload_2006 = serializers.analyze(D(2006, 1, 1))
        save_conclusion(D(2006, 1, 1), payload_2006)

        # 新增一条更晚（2010）的批单关系
        v3 = ClauseVersion.objects.create(
            clause_name="意外险", version_label="2010版",
            document=self.end2010)
        late = Relation.objects.create(
            kind="replace", document=self.end2010,
            from_version=v3, to_version=self.v2,
            date_earliest=D(2010, 5, 1), date_latest=D(2010, 5, 1))
        from endorsements.services import invalidate_downstream
        invalidate_downstream(late)

        c2003 = QueryConclusion.objects.get(query_date=D(2003, 1, 1))
        c2006 = QueryConclusion.objects.get(query_date=D(2006, 1, 1))
        self.assertFalse(c2003.stale, "早于批单的结论不应过期")
        self.assertFalse(c2006.stale, "2006 结论也不应受 2010 批单影响")

        # 而 2010 之后的结论（若存在）应过期
        payload_2011 = serializers.analyze(D(2011, 1, 1))
        save_conclusion(D(2011, 1, 1), payload_2011)
        invalidate_downstream(late)
        self.assertTrue(QueryConclusion.objects.get(
            query_date=D(2011, 1, 1)).stale)

    def test_editing_2005_relation_invalidates_2006_but_not_2003(self):
        save_conclusion(D(2003, 1, 1), serializers.analyze(D(2003, 1, 1)))
        save_conclusion(D(2006, 1, 1), serializers.analyze(D(2006, 1, 1)))
        from endorsements.services import invalidate_downstream
        invalidate_downstream(self.rel2005)
        self.assertFalse(QueryConclusion.objects.get(
            query_date=D(2003, 1, 1)).stale)
        self.assertTrue(QueryConclusion.objects.get(
            query_date=D(2006, 1, 1)).stale)

    # --- 5. 锁定关系删除返回 409 ----------------------------------------- #
    def test_delete_locked_relation_returns_409(self):
        self.rel2005.locked = True
        self.rel2005.save()
        r = self.c.delete(f"/api/relations/{self.rel2005.id}/")
        self.assertEqual(r.status_code, 409)
        self.assertIn("解锁", r.json()["error"])
        # 关系确实还在
        self.assertTrue(Relation.objects.filter(pk=self.rel2005.id).exists())

    def test_patch_locked_relation_returns_409(self):
        self.rel2005.locked = True
        self.rel2005.save()
        r = self.c.patch(f"/api/relations/{self.rel2005.id}/",
                         {"description": "x"}, content_type="application/json")
        self.assertEqual(r.status_code, 409)
