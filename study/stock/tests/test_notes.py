import pytest

import config
from core import yt, yt_clean


def segs(*texts, step=2.0):
    return [(i * step, step, t) for i, t in enumerate(texts)]


def test_fix_terms_variants():
    assert yt_clean.fix_terms("이 평선 눌림 목 손 익비 영매공파") == "이평선 눌림목 손익비 역매공파"


def test_noise_and_duplicate_removed():
    p = yt_clean.paragraphs(segs("[음악]", "안녕하세요 반갑습니다", "안녕하세요 반갑습니다"))
    assert len(p) == 1 and p[0][1] == "안녕하세요. 반갑습니다."


def test_overlapping_caption_tail_removed():
    p = yt_clean.paragraphs(segs("오늘은 이평선에 대해서 말씀드릴게요", "이평선에 대해서 말씀드릴게요 그러니까 중요해요"))
    assert p[0][1].count("말씀드릴게요") == 1


def test_sentence_split_and_question():
    p = yt_clean.paragraphs(segs("지금 사도 될까요 아닙니다 기다려야 합니다 손절은 필수예요"))
    assert p[0][1] == "지금 사도 될까요? 아닙니다. 기다려야 합니다. 손절은 필수예요."


def test_paragraph_breaks_on_silence_after_sentence():
    s = [(0, 2, "첫 문단입니다"), (10, 2, "둘째 문단입니다")]
    p = yt_clean.paragraphs(s)
    assert [x[1] for x in p] == ["첫 문단입니다.", "둘째 문단입니다."] and p[1][0] == 10


def test_tag_counts():
    c = yt_clean.tag_counts("눌림목 눌림목 지지선 거래량")
    assert c["눌림목"] == 2 and c["지지·저항"] == 1 and c["거래량"] == 1


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(yt, "DB_PATH", tmp_path / "yt.db")
    yt.init()
    with yt.conn() as c:
        c.executescript(yt_clean.SCHEMA)
        c.execute("INSERT INTO yt_videos(id,channel_id,kind,title,description,upload_date,duration,meta_status,caption_status,caption_kind,chapters) "
                  "VALUES('abc123XYZ_-','UC6ij59Gy_HnqO4pFu9A_zgQ','video','눌림목 정리','설명','2026-01-01',100,'ok','ok','auto','[]')")
        c.execute("INSERT INTO yt_videos(id,channel_id,kind,title,meta_status,caption_status) "
                  "VALUES('short123456','UC6ij59Gy_HnqO4pFu9A_zgQ','short','쇼츠 제목','ok','skipped')")
        c.execute("INSERT INTO yt_meta_fts VALUES('눌림목 정리','설명','abc123XYZ_-')")
        c.execute("INSERT INTO yt_segments VALUES('abc123XYZ_-',0,0,2,'눌림 목은 중요해요')")
        c.execute("INSERT INTO yt_paragraphs VALUES('abc123XYZ_-',0,5,'눌림목은 중요해요.')")
        c.execute("INSERT INTO yt_para_fts VALUES('눌림목은 중요해요.','abc123XYZ_-',5)")
        c.execute("INSERT INTO yt_tags VALUES('abc123XYZ_-','눌림목',11)")
    from app import create_app
    return create_app().test_client()


def test_search_finds_transcript_hit(client):
    html = client.get("/notes/?q=눌림목").get_data(as_text=True)
    assert "<mark>눌림목</mark>" in html and "abc123XYZ_-" in html


def test_two_char_search_uses_like(client):
    assert "abc123XYZ_-" in client.get("/notes/?q=눌림").get_data(as_text=True)


def test_search_input_is_escaped(client):
    r = client.get('/notes/?q="><script>x</script>')
    assert r.status_code == 200 and "<script>x</script>" not in r.get_data(as_text=True)


def test_kind_and_tag_filters(client):
    assert "쇼츠 제목" not in client.get("/notes/").get_data(as_text=True)
    assert "쇼츠 제목" in client.get("/notes/?kind=short").get_data(as_text=True)
    assert "abc123XYZ_-" in client.get("/notes/?tag=눌림목").get_data(as_text=True)
    assert "abc123XYZ_-" not in client.get("/notes/?tag=역매공파").get_data(as_text=True)


def test_detail_clean_and_raw(client):
    clean = client.get("/notes/abc123XYZ_-").get_data(as_text=True)
    assert "눌림목은 중요해요." in clean and "자동 생성 자막" in clean
    assert "눌림 목은" in client.get("/notes/abc123XYZ_-?raw=1").get_data(as_text=True)
    assert "자막을 수집하지 않습니다" in client.get("/notes/short123456").get_data(as_text=True)
    assert client.get("/notes/bad!id").status_code == 404
