"""
填入一套演示数据（含不确定日期、局部替换、增补、撤销、悬空/倒置问题各一例的
独立材料，方便首次启动时体验）。

用法：python manage.py seed_demo
重复执行会跳过（按文件编号判重）。
"""

from datetime import date

from django.core.management.base import BaseCommand

from endorsements.models import (ClauseParagraph, ClauseVersion, Relation,
                                 SourceDocument)


class Command(BaseCommand):
    help = "写入一套演示用旧保单/批单数据"

    def handle(self, *args, **options):
        if SourceDocument.all_objects.filter(file_no="DEMO-P-1999").exists():
            self.stdout.write("演示数据已存在，跳过。")
            return

        policy = SourceDocument.objects.create(
            file_no="DEMO-P-1999", title="人寿保险主保单（演示）",
            kind="policy",
            issued_earliest=date(1999, 3, 1), issued_latest=date(1999, 3, 1),
            effective_start_earliest=date(1999, 4, 1),
            effective_start_latest=date(1999, 4, 1),
            note="演示数据：旧版生效止日期纸质件上看不清，保持开口。")

        end2005 = SourceDocument.objects.create(
            file_no="DEMO-E-2005", title="责任条款批单（演示）",
            kind="endorsement",
            issued_earliest=date(2005, 5, 10), issued_latest=date(2005, 5, 10),
            effective_start_earliest=date(2005, 6, 1),
            effective_start_latest=date(2005, 6, 1))

        end2007 = SourceDocument.objects.create(
            file_no="DEMO-E-2007", title="住院津贴增补批单（演示）",
            kind="endorsement",
            issued_earliest=date(2007, 9, 1), issued_latest=date(2007, 9, 1),
            effective_start_earliest=date(2007, 10, 1),
            effective_start_latest=date(2007, 10, 1))

        end2009 = SourceDocument.objects.create(
            file_no="DEMO-E-2009", title="撤销批单（演示）",
            kind="endorsement",
            issued_earliest=date(2009, 2, 1), issued_latest=date(2009, 2, 1),
            effective_start_earliest=date(2009, 2, 10),
            effective_start_latest=date(2009, 2, 10))

        v1999 = ClauseVersion.objects.create(
            clause_name="意外伤害医疗保险条款（演示）",
            version_label="1999版", document=policy)
        p_a = ClauseParagraph.objects.create(
            version=v1999, paragraph_no="第一条", title="保险责任",
            text="（1999版原文）被保险人因意外伤害住院，按实际医疗费用给付。",
            order=1)
        p_b = ClauseParagraph.objects.create(
            version=v1999, paragraph_no="第二条", title="责任免除",
            text="（1999版原文）战争、醉酒、自残等情形不予给付。", order=2)
        p_c = ClauseParagraph.objects.create(
            version=v1999, paragraph_no="第三条", title="给付限额",
            text="（1999版原文）每年累计给付以保险金额为限。", order=3)

        v2005 = ClauseVersion.objects.create(
            clause_name="意外伤害医疗保险条款（演示）",
            version_label="2005局部批改版", document=end2005)
        p_a2 = ClauseParagraph.objects.create(
            version=v2005, paragraph_no="第一条", title="保险责任",
            text="（2005批改）在原责任基础上，增加门诊急诊费用给付，"
                 "免赔额100元。", order=1)

        v2007 = ClauseVersion.objects.create(
            clause_name="意外伤害医疗保险条款（演示）",
            version_label="2007增补版", document=end2007)
        ClauseParagraph.objects.create(
            version=v2007, paragraph_no="增补-住院津贴", title="住院津贴",
            text="（2007增补）住院期间每日给付津贴50元，与主条款并行适用。",
            order=1)

        v2009_revoked = ClauseVersion.objects.create(
            clause_name="意外伤害医疗保险条款（演示）",
            version_label="2009误录版", document=end2009,
            note="演示：此版本随后被撤销，旧文不自动恢复。")
        ClauseParagraph.objects.create(
            version=v2009_revoked, paragraph_no="第一条",
            title="保险责任（误录）", text="（误录文本，后被撤销）", order=1)

        r_partial = Relation.objects.create(
            kind="partial", document=end2005,
            from_version=v2005, to_version=v1999,
            date_earliest=date(2005, 5, 10), date_latest=date(2005, 5, 10),
            eff_start_earliest=date(2005, 6, 1),
            eff_start_latest=date(2005, 6, 1),
            description="仅批改第一条保险责任，第二、三条沿用1999版原文。")
        r_partial.affected_paragraphs.add(p_a2)

        r_supp = Relation.objects.create(
            kind="supplement", document=end2007,
            from_version=v2007, to_version=v2005,
            date_earliest=date(2007, 9, 1), date_latest=date(2007, 9, 1),
            description="增补住院津贴，与主条款并行。")

        r_bad = Relation.objects.create(
            kind="replace", document=end2009,
            from_version=v2009_revoked, to_version=v2005,
            date_earliest=date(2009, 2, 1), date_latest=date(2009, 2, 1),
            description="演示：一次误录的全文替换。")
        Relation.objects.create(
            kind="revoke_version", document=end2009,
            to_version=v2009_revoked,
            date_earliest=date(2009, 2, 10), date_latest=date(2009, 2, 10),
            description="撤销误录版（撤销只废止，不自动恢复任何旧文）。")

        # 一个看不清楚日期的关系示例（2004年底～2006年初之间发生）
        doc_uncertain = SourceDocument.objects.create(
            file_no="DEMO-E-????", title="日期不清的批单（演示）",
            kind="endorsement",
            issued_earliest=date(2004, 11, 1), issued_latest=date(2006, 2, 1))
        vu = ClauseVersion.objects.create(
            clause_name="演示-日期不确定条款",
            version_label="日期不清版", document=doc_uncertain)
        vu0 = ClauseVersion.objects.create(
            clause_name="演示-日期不确定条款",
            version_label="原始版", document=policy)
        ClauseParagraph.objects.create(
            version=vu, paragraph_no="第一条", text="（日期不清批改文本）",
            order=1)
        ClauseParagraph.objects.create(
            version=vu0, paragraph_no="第一条", text="（原始文本）", order=1)
        Relation.objects.create(
            kind="replace", document=doc_uncertain,
            from_version=vu, to_version=vu0,
            date_earliest=date(2004, 11, 1), date_latest=date(2006, 2, 1),
            description="演示：签发日看不清，结论会标注“可能”。")
        self.stdout.write(self.style.SUCCESS("演示数据已写入。"))
        self.stdout.write(
            "提示：去「按日期核对」分别试 2003-01-01 / 2006-01-01 / "
            "2008-01-01 / 2010-01-01。")
