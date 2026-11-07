from pathlib import Path
import unittest


SCRIPT=Path(__file__).with_name('windows_market_recovery.ps1')


class WindowsRecoveryTests(unittest.TestCase):
    def test_fixed_identity_and_expected_wsl_command(self):
        text=SCRIPT.read_text()
        self.assertIn('OuroMarket-25512088-Round3',text)
        self.assertIn('/home/ohs/projects/ai-agent-engineering-101/submissions/25512088/week-08',text)
        self.assertIn('bash run_ubuntu.sh supervisor start',text)
        self.assertIn('wsl.exe',text)

    def test_idempotent_install_and_conflict_preservation(self):
        text=SCRIPT.read_text()
        self.assertIn('already_installed',text)
        self.assertIn('if (-not $matches)',text)
        self.assertIn('conflict_preserved',text)
        self.assertIn('Unregister-ScheduledTask',text)
        # Registration must occur only after the existing-task conflict branch.
        self.assertLess(text.index('if ($null -ne $existing)'),text.index('Register-ScheduledTask'))
        self.assertLess(text.index('if (-not $matches)'),text.index('Register-ScheduledTask'))

    def test_actions_share_one_task_name(self):
        text=SCRIPT.read_text()
        self.assertIn('ValidateSet("install", "status", "remove")',text)
        self.assertEqual(text.count('$TaskName = "OuroMarket-25512088-Round3"'),1)
        self.assertNotIn('schtasks /Delete /TN *',text)


if __name__=='__main__':unittest.main()
