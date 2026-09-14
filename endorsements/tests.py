"""
核心引擎单元测试（python manage.py test endorsements）。

覆盖：区间日期判定、悬空引用、时间倒置、循环修改、同条款重叠、
局部替换保留未涉及段落、撤销不恢复旧文、多链并列、缺口输出。
"""

import datetime as dt

from django.test import TestCase

from . import engine

D = dt.date


def doc(i, no, **kw):
    base = dict(id=i, file_no=no, title=no, kind="other", scan_url=None,
                issued_lo=None, issued_hi=None, start_lo=None, start_hi=None,
                end_lo=None, end_hi=None, note="", deleted=False)
    base.update(kw)
    return base


def ver(i, name, doc_id, label="", pars=None, **kw):
    base = dict(id=i, clause_name=name, version_label=label, document_id=doc_id,
                doc_file_no=f"D{doc_id}", start_lo=None, start_hi=None,
                end_lo=None, end_hi=None, note="", deleted=False)
    base.update(kw)
    ps = []
    for j, (no, text) in enumerate(pars or [], start=1):
        ps.append(dict(id=i * 100 + j, paragraph_no=no, title="", text=text,
                       order=j, version_id=i))
    base["paragraphs"] = ps
    base["paragraph_ids"] = [p["id"] for p in ps]
    return base


def rel(i, kind, doc_id=99, from_id=None, to_id=None, target=None,
        affected=None, date_lo=None, date_hi=None, locked=False,
        deleted=False):
    return dict(id=i, kind=kind, document_id=doc_id, from_id=from_id,
                to_id=to_id, target_relation_id=target,
                affected_paragraph_ids=affected or [], description="",
                date_lo=date_lo, date_hi=date_hi,
                eff_start_lo=None, eff_start_hi=None,
                eff_end_lo=None, eff_end_hi=None,
                locked=locked, deleted=deleted)


def dataset(documents, versions, relations, regions=None):
    return {"documents": documents, "versions": versions,
            "relations": relations, "regions": regions or []}


class SpanTests(TestCase):
    def test_span_status(self):
        self.assertEqual(engine.span_status((D(2000, 1, 1), D(2000, 1, 1)),
                                             (None, None), D(2010, 6, 1)),
                         "active")
        self.assertEqual(engine.span_status((D(2020, 1, 1), None),
                                            (None, None), D(2010, 6, 1)),
                         "future")
        self.assertEqual(engine.span_status((D(2000, 1, 1), D(2000, 1, 1)),
                                            (D(2005, 1, 1), D(2005, 1, 1)),
                                            D(2010, 6, 1)), "expired")
        self.assertEqual(engine.span_status((D(2008, 1, 1), D(2012, 1, 1)),
                                            (None, None), D(2010, 6, 1)),
                         "possible")

    def test_occurred(self):
        self.assertEqual(engine.occurred((D(2005, 1, 1), D(2005, 1, 1)),
                                         D(2010, 1, 1)), "yes")
        self.assertEqual(engine.occurred((D(2008, 1, 1), D(2012, 1, 1)),
                                         D(2010, 1, 1)), "maybe")
        self.assertEqual(engine.occurred((D(2020, 1, 1), D(2020, 1, 1)),
                                         D(2010, 1, 1)), "no")


class CheckTests(TestCase):
    def setUp(self):
        self.docs = [doc(1, "P1", start_lo=D(1999, 1, 1)),
                     doc(2, "E1", issued_lo=D(2005, 1, 1)),
                     doc(99, "X", start_lo=D(1990, 1, 1))]

    def test_dangling(self):
        ds = dataset(self.docs, [ver(1, "意外险", 1)],
                     [rel(1, "replace", from_id=1, to_id=999)])
        codes = [i["code"] for i in engine.run_checks(ds)]
        self.assertIn("dangling", codes)

    def test_inverted_date(self):
        bad = doc(3, "BAD", issued_lo=D(2010, 1, 1), issued_hi=D(2000, 1, 1))
        codes = [i["code"] for i in engine.run_checks(dataset(
            self.docs + [bad], [], []))]
        self.assertIn("inverted_date", codes)

    def test_relation_before_version(self):
        ds = dataset(
            self.docs,
            [ver(1, "意外险", 1, "旧版", start_lo=D(2000, 1, 1)),
             ver(2, "意外险", 2, "新版")],
            [rel(1, "replace", from_id=2, to_id=1,
                 date_lo=D(1995, 1, 1), date_hi=D(1995, 6, 1))])
        issues = engine.run_checks(ds)
        self.assertTrue(any(i["code"] == "inverted_date" for i in issues))

    def test_cycle(self):
        ds = dataset(
            self.docs,
            [ver(1, "意外险", 1), ver(2, "意外险", 2), ver(3, "意外险", 2)],
            [rel(1, "replace", from_id=2, to_id=1, date_lo=D(2001, 1, 1)),
             rel(2, "replace", from_id=3, to_id=2, date_lo=D(2002, 1, 1)),
             rel(3, "replace", from_id=1, to_id=3, date_lo=D(2003, 1, 1))])
        self.assertIn("cycle", [i["code"] for i in engine.run_checks(ds)])

    def test_overlap_unlinked_is_error(self):
        ds = dataset(
            self.docs,
            [ver(1, "意外险", 1, "A",
                 start_lo=D(2000, 1, 1), start_hi=D(2000, 1, 1),
                 end_lo=D(2010, 1, 1), end_hi=D(2010, 1, 1)),
             ver(2, "意外险", 2, "B",
                 start_lo=D(2005, 1, 1), start_hi=D(2005, 1, 1),
                 end_lo=D(2020, 1, 1), end_hi=D(2020, 1, 1))],
            [])
        issues = [i for i in engine.run_checks(ds) if i["code"] == "overlap"]
        self.assertTrue(issues and issues[0]["level"] == "error")


class EvolutionTests(TestCase):
    def setUp(self):
        self.docs = [
            doc(1, "P1", kind="policy", start_lo=D(1999, 1, 1),
                start_hi=D(1999, 1, 1)),
            doc(2, "E1", kind="endorsement", issued_lo=D(2005, 3, 1),
                start_lo=D(2005, 4, 1), start_hi=D(2005, 4, 1)),
            doc(3, "E2", kind="endorsement", issued_lo=D(2008, 5, 1),
                start_lo=D(2008, 6, 1), start_hi=D(2008, 6, 1)),
        ]
        self.versions = [
            ver(1, "意外险", 1, "1999版",
                [("第一条", "旧A"), ("第二条", "旧B"), ("第三条", "旧C")],
                start_lo=D(1999, 1, 1), start_hi=D(1999, 1, 1)),
            ver(2, "意外险", 2, "2005局部版",
                [("第二条", "新B")],
                start_lo=D(2005, 4, 1), start_hi=D(2005, 4, 1)),
            ver(3, "意外险", 3, "2008全文版",
                [("第一条", "全A"), ("第二条", "全B"), ("第三条", "全C")],
                start_lo=D(2008, 6, 1), start_hi=D(2008, 6, 1)),
        ]

    def test_partial_replace_keeps_uninvolved_paragraphs(self):
        ds = dataset(self.docs, self.versions, [
            rel(1, "partial", doc_id=2, from_id=2, to_id=1,
                affected=[201], date_lo=D(2005, 4, 1), date_hi=D(2005, 4, 1)),
        ])
        out = engine.analyze(ds, D(2006, 1, 1))
        clause = out["clauses"][0]
        self.assertEqual(clause["status"], "effective")
        paras = {p["paragraph_no"]: p["text"]
                 for p in clause["candidates"][0]["paragraphs_effective"]}
        self.assertEqual(paras, {"第一条": "旧A", "第二条": "新B", "第三条": "旧C"})

    def test_full_replace_then_revoke_does_not_restore(self):
        docs = [self.docs[0], self.docs[2]]
        versions = [self.versions[0], self.versions[2]]
        ds = dataset(docs, versions, [
            rel(1, "replace", doc_id=3, from_id=3, to_id=1,
                date_lo=D(2008, 6, 1), date_hi=D(2008, 6, 1)),
            rel(2, "revoke_version", doc_id=3, to_id=3,
                date_lo=D(2009, 1, 1), date_hi=D(2009, 1, 1)),
        ])
        out = engine.analyze(ds, D(2010, 1, 1))
        clause = out["clauses"][0]
        self.assertEqual(clause["status"], "revoked_gap")
        self.assertTrue(clause["candidates"][0]["paragraphs_effective"] == [])
        self.assertTrue(any(g["kind"] == "revoked" for g in out["gaps"]))

    def test_revoke_relation_does_not_restore(self):
        ds = dataset(self.docs, self.versions, [
            rel(1, "replace", doc_id=3, from_id=3, to_id=1,
                date_lo=D(2008, 6, 1), date_hi=D(2008, 6, 1)),
            rel(2, "revoke_relation", doc_id=3, target=1,
                date_lo=D(2009, 1, 1), date_hi=D(2009, 1, 1)),
        ])
        out = engine.analyze(ds, D(2010, 1, 1))
        clause = out["clauses"][0]
        texts = {p["paragraph_no"]: p["text"]
                 for p in clause["candidates"][0]["paragraphs_effective"]}
        # 替换被撤销后，2008全文版不作为"生效替换"，链回到1999版；
        # 注意：此处旧版生效区间开口，故旧文继续沿用（材料明确记载仍有效）
        self.assertEqual(texts.get("第一条"), "旧A")
        # 图中被撤销的边必须带 revoked 标记，供人工核对，不做自动恢复宣告
        graph = clause["graph"]
        edge = next(e for e in graph["edges"] if e["id"] == 1)
        self.assertEqual(edge["revoked"], "yes")

    def test_parallel_compatible_chains_shown_side_by_side(self):
        # 同一条款在同一日有两条互不相干的替换链分叉（来源不清）
        docs = self.docs + [doc(4, "E3", kind="endorsement",
                                issued_lo=D(2007, 1, 1),
                                start_lo=D(2007, 1, 1),
                                start_hi=D(2007, 1, 1))]
        versions = self.versions + [
            ver(4, "意外险", 4, "另一2007版", [("第一条", "分叉A")],
                start_lo=D(2007, 1, 1), start_hi=D(2007, 1, 1)),
        ]
        ds = dataset(docs, versions, [
            rel(1, "replace", doc_id=2, from_id=2, to_id=1,
                date_lo=D(2005, 4, 1), date_hi=D(2005, 4, 1)),
            rel(2, "replace", doc_id=4, from_id=4, to_id=1,
                date_lo=D(2007, 1, 1), date_hi=D(2007, 1, 1)),
        ])
        out = engine.analyze(ds, D(2007, 6, 1))
        clause = out["clauses"][0]
        self.assertGreaterEqual(clause["candidate_count"], 2)

    def test_uncertain_date_makes_candidate_possible(self):
        versions = [self.versions[0], self.versions[1]]
        ds = dataset(self.docs[:2], versions, [
            rel(1, "partial", doc_id=2, from_id=2, to_id=1,
                affected=[201], date_lo=D(2004, 1, 1), date_hi=D(2007, 1, 1)),
        ])
        out = engine.analyze(ds, D(2005, 6, 1))
        cands = out["clauses"][0]["candidates"]
        # 关系是否已发生不确定 → 两条世界线并列，均标注"可能"
        self.assertGreaterEqual(len(cands), 2)
        self.assertTrue(all(c["certainty"] == "possible" for c in cands))
        paths = [tuple(n["version_id"] for n in c["chain"]) for c in cands]
        self.assertIn((2, 1), paths)   # 修改已发生
        self.assertIn((1,), paths)     # 修改尚未发生（旧版仍适用）
