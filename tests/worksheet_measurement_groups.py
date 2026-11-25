"""Recorded condition and group addressing must agree across the GUI and assistant."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.worksheet_entry import entry_groups, samples
from app.worksheet_interaction import prepare_edit


class MeasurementGroups(unittest.TestCase):
    def setUp(self):
        self.pair = {"condition": "load", "variant": "direction", "variants": ["加载", "卸载"]}
        self.table = {"id": "spring", "title": "弹性测量", "entry_mode": "groups",
                      "grouping": self.pair, "columns": [
                          {"id":"load","label":"载荷","unit":"N"},
                          {"id":"direction","label":"方向","unit":"文本","type":"text"},
                          {"id":"distance","label":"位移","unit":"mm"}]}
        self.obj = {"content":{"pages":[{"id":"p","fields":[],"tables":[self.table]}]}}
        self.rows = [{"load":str(force),"direction":direction,"distance":str(i)}
                     for i,(force,direction) in enumerate([(1,"加载"),(2,"加载"),(1,"卸载"),(2,"卸载")],1)]
        self.original = {"p":{"tables":{"spring":{"rows":self.rows}}}}

    def edited(self, **action):
        _, updated, _ = prepare_edit(self.obj, self.original, {"page":"p","table":"spring",**action})
        return updated["p"]["tables"]["spring"]["rows"]

    def test_group_and_raw_address_refer_to_different_observations(self):
        self.assertEqual(entry_groups(self.rows,self.pair), [[0,2],[1,3]])
        before=copy.deepcopy(self.rows)
        rows=self.edited(action="cells", cells=[{"group":2,"column":"distance","variant":"卸载","value":"0.8","unit":"cm"},
                                                {"group":2,"column":"load","value":"4"}])
        self.assertEqual([r["distance"] for r in rows],["1","2","3","8"])
        self.assertEqual([r["load"] for r in rows],["1","4","1","4"])
        self.assertEqual(self.rows,before)
        with self.assertRaises(ValueError):
            self.edited(action="cells",cells=[{"group":2,"column":"distance","value":"7"}])
        self.assertEqual(self.edited(action="cells",cells=[{"row":2,"column":"distance","value":"7"}])[1]["distance"],"7")

    def test_group_adds_two_empty_measurements_and_excludes_both(self):
        added=self.edited(action="rows",delta=1)
        self.assertEqual(added[4:],[{"direction":"加载"},{"direction":"卸载"}])
        self.assertEqual(entry_groups(added,self.pair),[[0,2],[1,3],[4,5]])
        self.assertEqual([i for i,r in enumerate(self.edited(action="rows",delta=-1)) if r.get("_excluded")],[1,3])
        self.assertEqual(len(self.edited(action="rows",delta=1,count_unit="rows")),5)

    def test_never_fabricates_branch_or_matches_ambiguous_repeats(self):
        self.rows.pop()
        with self.assertRaises(ValueError):
            self.edited(action="cells",cells=[{"group":2,"variant":"卸载","column":"distance","value":"7"}])
        same=[{"load":"1","direction":direction} for direction in ["加载","加载","加载","卸载"]]
        self.assertEqual(entry_groups(same,self.pair),[[0],[1],[2,3]])
        self.assertEqual(entry_groups([{},{}],self.pair),[[0,1]])

    def test_manual_stopwatch_and_time_series_have_distinct_default_surfaces(self):
        t={"id":"pendulum","title":"平行测定","columns":[{"id":"duration","label":"十个周期用时","unit":"s"}]}
        self.assertFalse(samples(t,{}))
        t.update(title="示波器波形",columns=[{"id":"t","label":"时刻","unit":"ms"}])
        self.assertTrue(samples(t,{}))
        self.assertFalse(samples({**t,"entry_mode":"groups"},{}))


if __name__=="__main__":
    unittest.main()
