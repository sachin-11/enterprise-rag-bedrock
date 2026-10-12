from app.models.chat import SourceCitation
from app.services import cache_service


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def lpush(self, *args):
        self.ops.append(("lpush", args))
        return self

    def ltrim(self, *args):
        self.ops.append(("ltrim", args))
        return self

    def expire(self, *args):
        self.ops.append(("expire", args))
        return self

    def sadd(self, *args):
        self.ops.append(("sadd", args))
        return self

    def execute(self):
        for name, args in self.ops:
            getattr(self.redis, name)(*args)


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.sets = {}

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    def expire(self, key, ttl):
        return True

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    def lrange(self, key, start, end):
        return self.lists.get(key, [])

    def smembers(self, key):
        return self.sets.get(key, set())

    def delete(self, *keys):
        for key in keys:
            self.lists.pop(key, None)
            self.sets.pop(key, None)


def test_redis_cache_is_scoped_and_shared(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(cache_service.settings, "semantic_cache_backend", "redis")
    monkeypatch.setattr(cache_service, "get_redis_client", lambda: redis)
    source = SourceCitation(chunk_id="c1", doc_name="policy.pdf", excerpt="Policy")

    cache_service.store("tenant-a", "user-a", False, "policy", [1.0, 0.0], "answer-a", [source])

    assert cache_service.lookup("tenant-a", "user-a", False, [1.0, 0.0]).answer_text == "answer-a"
    assert cache_service.lookup("tenant-a", "user-b", False, [1.0, 0.0]) is None
    assert cache_service.lookup("tenant-b", "user-a", False, [1.0, 0.0]) is None
    assert cache_service.lookup("tenant-a", "user-a", True, [1.0, 0.0]) is None


def test_redis_tenant_invalidation_does_not_touch_other_tenant(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(cache_service.settings, "semantic_cache_backend", "redis")
    monkeypatch.setattr(cache_service, "get_redis_client", lambda: redis)
    cache_service.store("tenant-a", "u1", False, "q", [1.0], "a", [])
    cache_service.store("tenant-b", "u1", False, "q", [1.0], "b", [])

    cache_service.invalidate_tenant("tenant-a")

    assert cache_service.lookup("tenant-a", "u1", False, [1.0]) is None
    assert cache_service.lookup("tenant-b", "u1", False, [1.0]).answer_text == "b"
