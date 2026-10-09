"""Command menu: scrolling clamps, and the daemon's cmdkey navigation runs the selected action."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import command_view as CV
from dc32host import daemon as D


class View(unittest.TestCase):
    def test_move_clamps_and_render(self):
        v = CV.CommandView()
        v.move(-5)
        self.assertEqual(v.sel, 0)
        v.move(9999)
        self.assertEqual(v.sel, len(CV.COMMANDS) - 1)
        self.assertEqual(v.action(), CV.COMMANDS[-1][1])
        self.assertEqual(v.render().shape, (240, 320, 3))


class Nav(unittest.TestCase):
    def stub(self):
        d = D.Daemon.__new__(D.Daemon)
        d.view, d.prev_view, d.cmd = 'cmd', 'mirror', CV.CommandView()
        d.ran, d.viewed = [], []
        d.action = lambda a: D.Daemon.action(d, a)      # real action dispatch
        d.set_view = lambda v: (d.viewed.append(v), setattr(d, 'view', v))
        d.force_refresh = lambda: None
        return d

    def test_arrows_enter_esc(self):
        d = self.stub()
        D.Daemon.action(d, 'cmdkey:Down')
        D.Daemon.action(d, 'cmdkey:Down')
        self.assertEqual(d.cmd.sel, 2)
        self.assertEqual(d.cmd.action(), CV.COMMANDS[2][1])     # a real action name
        # Enter on a view command routes through set_view
        want = CV.COMMANDS[2][1]
        D.Daemon.action(d, 'cmdkey:Return')
        # the action either changed the view or stayed (adjust); for COMMANDS[2]=flipper it opens a view
        self.assertTrue(d.viewed or d.view != 'cmd')
        d2 = self.stub()
        D.Daemon.action(d2, 'cmdkey:Escape')
        self.assertEqual(d2.view, 'mirror')

    def test_cmdkey_ignored_when_not_in_cmd_view(self):
        d = self.stub(); d.view = 'mirror'
        D.Daemon.action(d, 'cmdkey:Down')
        self.assertEqual(d.cmd.sel, 0)



    def test_jump_to_letter(self):
        v = CV.CommandView()
        self.assertTrue(v.jump('r'))                 # first R-command (Runner costs)
        self.assertTrue(CV.COMMANDS[v.sel][0].lower().startswith('r'))
        first = v.sel
        v.jump('r')                                  # again -> next R-command (wraps through)
        self.assertTrue(CV.COMMANDS[v.sel][0].lower().startswith('r'))
        self.assertFalse(v.jump('q'))                # no command starts with q

if __name__ == '__main__':
    unittest.main()
