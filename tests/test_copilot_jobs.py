"""Contracts for job clustering + legibility-floor packing (copilot/jobs.py)."""

import unittest
from types import SimpleNamespace

from agent_terminal.copilot import jobs


def sess(id, start, end, *, project=None, cmds=5, title=None):
    return SimpleNamespace(id=id, started_at=float(start), ended_at=float(end),
                           project=project, command_count=cmds,
                           title=title or id, cwd_last=f"/home/x/{project or id}")


# A monospace cell at a common size; the floor is 80x24 by default.
CELL = (9, 18)                       # ~ min pane = 720 x 432 px
# The user's three real display setups (usable pane area ≈ monitor minus chrome).
LAPTOP_14 = (1512, 950)              # 14" laptop
ULTRAWIDE = (3440, 1440)            # 3440x1440
WIDE_38 = (3840, 1600)             # 3840x1600


class ClusterTests(unittest.TestCase):
    def test_overlapping_sessions_are_one_job(self):
        # server + client + editor, all active in the same window of time.
        s = [sess("server", 1000, 2000, project="app"),
             sess("client", 1100, 1900, project="app"),
             sess("editor", 1050, 2050, project="app")]
        js = jobs.cluster(s)
        self.assertEqual(len(js), 1)
        self.assertEqual(js[0].size, 3)
        self.assertEqual(js[0].project, "app")
        self.assertEqual(js[0].title, "app")

    def test_disjoint_far_apart_sessions_are_separate(self):
        s = [sess("morning", 1000, 1100, project="a"),
             sess("evening", 100000, 100100, project="b")]
        # two singletons -> no job (min_size 2)
        self.assertEqual(jobs.cluster(s), [])

    def test_near_touch_within_gap_links(self):
        # a 4-minute gap (< default 5-min) links even across projects.
        s = [sess("a", 1000, 2000, project="a"),
             sess("b", 2000 + 240, 3000, project="b")]
        self.assertEqual(len(jobs.cluster(s)), 1)

    def test_same_project_gets_wider_gap(self):
        # 12-minute gap sits between the cross-project tolerance (5 min) and
        # the same-project one (15 min): links only within a project.
        gap = 12 * 60
        cross = [sess("a", 1000, 2000, project="a"),
                 sess("b", 2000 + gap, 3000 + gap, project="b")]
        self.assertEqual(jobs.cluster(cross), [])
        same = [sess("a", 1000, 2000, project="proj"),
                sess("b", 2000 + gap, 3000 + gap, project="proj")]
        self.assertEqual(len(jobs.cluster(same)), 1)

    def test_gap_threshold_sensitivity(self):
        # the same two sessions merge at a loose gap, split at a tight one.
        s = [sess("a", 1000, 2000, project="x"),
             sess("b", 2200, 3000, project="y")]     # 200s apart
        self.assertEqual(len(jobs.cluster(s, gap_s=300)), 1)
        self.assertEqual(jobs.cluster(s, gap_s=100), [])   # both singletons

    def test_mixed_project_title_is_busiest_member(self):
        s = [sess("a", 1000, 2000, project="a", cmds=2),
             sess("b", 1100, 2100, project="b", cmds=40, title="b: cargo")]
        job = jobs.cluster(s)[0]
        self.assertIsNone(job.project)
        self.assertEqual(job.title, "b: cargo")

    def test_members_sorted_by_start(self):
        s = [sess("late", 1500, 2500, project="p"),
             sess("early", 1000, 2000, project="p")]
        job = jobs.cluster(s)[0]
        self.assertEqual([m.id for m in job.members], ["early", "late"])


class CapacityTests(unittest.TestCase):
    def test_capacity_scales_with_display(self):
        cap = lambda avail: jobs.capacity(avail, CELL)
        self.assertEqual(cap(LAPTOP_14), 4)     # 2 cols x 2 rows
        self.assertEqual(cap(ULTRAWIDE), 6)     # 4 x 3 = 12, soft-capped at 6
        self.assertEqual(cap(WIDE_38), 6)       # 5 x 3 = 15, capped at 6

    def test_soft_max_caps(self):
        self.assertEqual(jobs.capacity(WIDE_38, CELL, soft_max=8), 8)
        self.assertEqual(jobs.capacity(WIDE_38, CELL, soft_max=4), 4)

    def test_floor_is_never_violated(self):
        # a tiny window still yields at least one (full) pane, never zero.
        self.assertEqual(jobs.capacity((400, 300), CELL), 1)


class PackTests(unittest.TestCase):
    def _members(self, n):
        return [sess(f"s{i}", 1000 + i, 2000 + i, project="p") for i in range(n)]

    def test_same_job_packs_differently_per_display(self):
        six = self._members(6)
        # ultrawide: one window of 6
        wide = jobs.pack(six, avail_px=ULTRAWIDE, cell_px=CELL)
        self.assertEqual([p.size for p in wide], [6])
        # laptop: capacity 4 -> two balanced windows [3, 3]
        lap = jobs.pack(six, avail_px=LAPTOP_14, cell_px=CELL)
        self.assertEqual([p.size for p in lap], [3, 3])

    def test_eight_on_laptop_is_two_fours_not_one_eight(self):
        plans = jobs.pack(self._members(8), avail_px=LAPTOP_14, cell_px=CELL)
        self.assertEqual([p.size for p in plans], [4, 4])
        self.assertTrue(all(p.size <= 4 for p in plans))

    def test_all_members_preserved_and_ordered(self):
        members = self._members(7)
        plans = jobs.pack(members, avail_px=LAPTOP_14, cell_px=CELL)
        flat = [m for p in plans for m in p.members]
        self.assertEqual(flat, members)                 # order + completeness

    def test_grid_never_exceeds_what_fits(self):
        plans = jobs.pack(self._members(6), avail_px=LAPTOP_14, cell_px=CELL)
        for p in plans:
            self.assertLessEqual(p.cols * p.rows >= p.size, True)
            self.assertLessEqual(p.cols, 2)             # laptop fits 2 cols
            self.assertLessEqual(p.rows, 2)

    def test_empty(self):
        self.assertEqual(jobs.pack([], avail_px=WIDE_38, cell_px=CELL), [])


if __name__ == "__main__":
    unittest.main()
