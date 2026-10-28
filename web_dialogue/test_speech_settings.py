import unittest

from .speech_settings import (DEFAULT_RETRY_ATTEMPTS, MAX_RETRY_ATTEMPTS,
                              retry_delay_seconds, segment_timeout_seconds,
                              validate_retry_attempts)


class SpeechSettingsTests(unittest.TestCase):
    def test_default_and_valid_limits(self):
        self.assertEqual(DEFAULT_RETRY_ATTEMPTS,5)
        self.assertEqual(validate_retry_attempts(1),1)
        self.assertEqual(validate_retry_attempts(MAX_RETRY_ATTEMPTS),30)

    def test_attempt_limits_reject_non_integer_and_out_of_range_values(self):
        for value in (0,31,1.0,True,False,'5',None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_retry_attempts(value)

    def test_retry_delay_is_progressive_then_capped_at_two_seconds(self):
        self.assertEqual([retry_delay_seconds(n) for n in (1,2,3,4,5,30)],[.5,1,1.5,2,2,2])

    def test_segment_deadline_scales_with_attempt_limit_and_caps_at_five_minutes(self):
        self.assertEqual(segment_timeout_seconds(5),90)
        self.assertEqual(segment_timeout_seconds(9),90)
        self.assertEqual(segment_timeout_seconds(10),100)
        self.assertEqual(segment_timeout_seconds(30),300)


if __name__ == '__main__': unittest.main()
