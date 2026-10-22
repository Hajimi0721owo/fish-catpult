import unittest
from bili_fav_relay.vendor.bili_api import BiliError, media_urls, select_streams


class MediaURLTest(unittest.TestCase):
    def test_nonstandard_candidate_does_not_break_safe_cdn(self):
        item = {'base_url': 'https://edge.mcdn.bilivideo.cn:8082/video',
                'backup_url': ['https://upos.bilivideo.com/video']}
        self.assertEqual(media_urls(item), ['https://upos.bilivideo.com/video'])

    def test_unsafe_candidates_remain_rejected(self):
        item = {'url': 'http://127.0.0.1/media',
                'backup_url': ['https://evil.example/media', 'https://upos.bilivideo.com:8082/media']}
        self.assertEqual(media_urls(item), [])

    def test_declared_audio_cannot_silently_disappear(self):
        play = {'quality': 16, 'dash': {
            'video': [{'id': 16, 'base_url': 'https://upos.bilivideo.com/v'}],
            'audio': [{'id': 30280, 'base_url': 'http://127.0.0.1/audio'}]}}
        with self.assertRaisesRegex(BiliError, 'audio'):
            select_streams(play)
