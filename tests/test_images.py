"""Image link cleaning: only safe https links on allowed hosts may reach the page. No network."""
import pytest

from app import images
from app.images import clean_image_ref, detail_url, hosts_from_env, pick_image

OK = "https://sm.config.inc/files/a.png"


# ----------------------------------------------------------------------------- clean_image_ref
@pytest.mark.parametrize("url", [
    OK,
    "https://sm.config.inc",
    "https://sm.config.inc/a.png?size=2&v=1#top",
    "https://cdn.sm.config.inc/a.png",                 # subdomain of an allowed host
    "https://a.b.sm.config.inc/a.png",
    "HTTPS://SM.Config.INC/A.png",                     # case does not matter for scheme or host
    "https://sm.config.inc:443/a.png",
])
def test_accepts_https_on_allowed_host(url):
    assert clean_image_ref(url) == url


def test_strips_surrounding_whitespace():
    assert clean_image_ref(f"  {OK} \n") == OK
    assert clean_image_ref("\t/img/a.png ") == "https://sm.config.inc/img/a.png"


@pytest.mark.parametrize("url", [
    "https://sm.config.inc.evil.com/x",                # allowed name as a prefix
    "https://evil.com/sm.config.inc",                  # allowed name in the path
    "https://evil.com/?u=sm.config.inc",
    "https://evil.com#sm.config.inc",
    "https://sm.config.inc@evil.com/",                 # userinfo trick
    "https://user:pw@sm.config.inc/x",                 # any userinfo is refused
    "https://sm.config.inc:443@evil.com/",
    "https://evil.com@sm.config.inc/",
    "https://notsm.config.inc/x",                      # same ending, not a subdomain
    "https://xsm.config.inc/x",
    "https://config.inc/x",                            # parent of the allowed host
    "https://inc/x",
    "https://sm.config.inc%2eevil.com/x",
    "https://sm.config.inc．evil.com/x",           # full-width dot
    "https://sm.config.inc./x",                        # trailing dot is not an exact match
    "https://sm.config.inc:8443/x",                    # odd port
    "https://sm.config.inc:abc/x",
    "https://[sm.config.inc/x",                        # broken ipv6 brackets
    "https://[::1]/x",
    "https://127.0.0.1/x",
])
def test_rejects_sneaky_hosts(url):
    assert clean_image_ref(url) is None


@pytest.mark.parametrize("url", [
    "http://sm.config.inc/a.png",
    "ftp://sm.config.inc/a.png",
    "data:image/png;base64,AAAA",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "blob:https://sm.config.inc/6f1c",
    "file:///etc/passwd",
    "//sm.config.inc/a.png",                           # protocol-relative
    "//evil.com/a.png",
    "///evil.com/a.png",
    "sm.config.inc/a.png",                             # no scheme
    "images/a.png",                                    # relative without a leading slash
    "./a.png",
    "https:sm.config.inc/a.png",
    "https:/sm.config.inc/a.png",
    "https:///sm.config.inc/a.png",
    "https://",
    "https:",
])
def test_rejects_bad_schemes_and_shapes(url):
    assert clean_image_ref(url) is None


@pytest.mark.parametrize("url", [
    "https://sm.config.inc/a\x00.png",
    "https://sm.config.inc/a\x07.png",
    "https://sm.config.inc/a\x7f.png",
    "https://sm.config.inc/a.png\x00",                 # not whitespace, so not stripped
    "https://sm.config.inc/a\n.png",
    "https://sm.config.inc/a\r\n.png",
    "https://sm.config.inc/a\t.png",
    "https://sm.config.inc/a b.png",                   # space inside
    "https://sm.config.inc/a b.png",              # no-break space inside
    "https://sm.config.inc/a​.png",               # zero-width space
    "https://sm.config.inc/a‮.png",               # right-to-left override
    "https://sm.config.inc/a\\b.png",                  # backslash
    "https://sm.config.inc\\@evil.com/",
    "/\\evil.com/a.png",
    "/img/a\x00.png",
    "/img/a b.png",
    'https://sm.config.inc/a".png',                    # could end an HTML attribute
    "https://sm.config.inc/a'.png",
    "https://sm.config.inc/<b>.png",
    "https://sm.config.inc/a`.png",
    "j\navascript:alert(1)",
])
def test_rejects_control_whitespace_and_quote_characters(url):
    assert clean_image_ref(url) is None


def test_length_limit_is_2000():
    head = "https://sm.config.inc/"
    assert clean_image_ref(head + "a" * (2000 - len(head))) is not None
    assert clean_image_ref(head + "a" * (2001 - len(head))) is None
    assert clean_image_ref("/" + "a" * 2000) is None
    assert clean_image_ref("/" + "a" * 1999) is not None


S3 = "https://configint-main.s3.amazonaws.com/scenario-manager/cache/objects/O004719.jpg"
SIGNED = S3 + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE%2F20261110%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20261110T100000Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Security-Token=" + "IQoJb3JpZ2luX2Vj" * 50 + "&X-Amz-Signature=" + "ab12" * 16   # temporary credentials add a very long token


def test_sm_picture_bucket_is_allowed_including_long_signed_links():
    assert clean_image_ref(S3) == S3
    assert clean_image_ref(SIGNED) == SIGNED and len(SIGNED) > 500            # the old 500 limit would have refused every real link
    assert pick_image({"image_url": SIGNED, "heldout_product_image_url": ""}) == SIGNED
    assert pick_image({"image_url": "", "heldout_product_image_url": S3}) is None   # only the listed keys are read


@pytest.mark.parametrize("url", [
    "https://evil.s3.amazonaws.com/scenario-manager/cache/objects/O004719.jpg",               # another bucket
    "https://s3.amazonaws.com/configint-main/scenario-manager/cache/objects/O004719.jpg",     # path-style address is a different host
    "https://configint-main.s3.amazonaws.com.evil.com/x.jpg",
    "https://configint-main.s3.amazonaws.com@evil.com/x.jpg",
    "http://configint-main.s3.amazonaws.com/x.jpg",
    "https://amazonaws.com/configint-main/x.jpg",
])
def test_only_the_sm_bucket_is_allowed_not_s3_in_general(url):
    assert clean_image_ref(url) is None


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t", 0, 5, 1.5, True, b"https://sm.config.inc/a.png",
                                   [], [OK], {}, {"url": OK}, ("x",), object()])
def test_non_strings_and_empty_give_none(value):
    assert clean_image_ref(value) is None


def test_site_relative_paths_use_base():
    assert clean_image_ref("/files/a.png") == "https://sm.config.inc/files/a.png"
    assert clean_image_ref("/a.png?x=1#y") == "https://sm.config.inc/a.png?x=1#y"
    assert clean_image_ref("/") == "https://sm.config.inc/"
    assert clean_image_ref("/a.png", base="https://sm.config.inc/") == "https://sm.config.inc/a.png"
    assert clean_image_ref("/a.png", base="https://other.test") == "https://other.test/a.png"


@pytest.mark.parametrize("url", ["//evil.com/a.png", "//sm.config.inc/a.png", "///x", "/\\evil.com"])
def test_site_relative_never_leaves_the_site(url):
    assert clean_image_ref(url) is None


def test_custom_hosts():
    hosts = ("Cdn.Example.com", "sm.config.inc")
    assert clean_image_ref("https://cdn.example.com/a.png", hosts=hosts)
    assert clean_image_ref("https://img.cdn.example.com/a.png", hosts=hosts)
    assert clean_image_ref(OK, hosts=hosts) == OK
    assert clean_image_ref("https://example.com/a.png", hosts=hosts) is None
    assert clean_image_ref(OK, hosts=("cdn.example.com",)) is None     # the default is replaced
    assert clean_image_ref(OK, hosts=()) is None
    # blank entries must not turn into a wildcard
    assert clean_image_ref("https://evil.com./a.png", hosts=("", " ")) is None


# ----------------------------------------------------------------------------- pick_image
KEYS = ["image_url", "imageUrl", "image", "thumbnail_url", "thumbnailUrl", "thumbnail", "thumb",
        "photo_url", "photo", "picture", "img", "img_url", "images", "files", "attachments"]


def test_key_list_and_order():
    assert list(images.IMAGE_KEYS) == KEYS


@pytest.mark.parametrize("key", KEYS)
def test_every_key_is_read(key):
    assert pick_image({"name": "Box", key: OK}) == OK
    assert pick_image({key: "/files/a.png"}) == "https://sm.config.inc/files/a.png"


def test_key_priority():
    row = {"files": "https://sm.config.inc/files.png", "thumb": "https://sm.config.inc/thumb.png",
           "image": "https://sm.config.inc/image.png"}
    assert pick_image(row) == "https://sm.config.inc/image.png"
    row["image_url"] = "https://sm.config.inc/first.png"
    assert pick_image(row) == "https://sm.config.inc/first.png"


def test_unsafe_value_falls_through_to_next_key():
    row = {"image_url": "javascript:alert(1)", "imageUrl": "http://sm.config.inc/a.png",
           "image": "https://evil.com/a.png", "thumb": None, "photo": 12, "picture": "",
           "img": ["//evil.com/x"], "images": [{"url": "data:image/png;base64,AA"}],
           "files": [{"src": "/files/ok.png"}]}
    assert pick_image(row) == "https://sm.config.inc/files/ok.png"


@pytest.mark.parametrize("key", ["url", "src", "path", "href", "image_url"])
def test_dict_value_with_each_url_key(key):
    assert pick_image({"image": {key: OK, "alt": "x"}}) == OK


def test_dict_url_key_order_and_fallback():
    value = {"href": "https://sm.config.inc/href.png", "url": "https://sm.config.inc/url.png"}
    assert pick_image({"image": value}) == "https://sm.config.inc/url.png"
    value = {"url": "javascript:alert(1)", "src": "/ok.png"}
    assert pick_image({"image": value}) == "https://sm.config.inc/ok.png"


def test_list_takes_first_that_cleans():
    row = {"images": ["javascript:x", "", None, 7, {"url": "https://evil.com/a.png"},
                      {"foo": OK}, {"src": "/second.png"}, "/third.png"]}
    assert pick_image(row) == "https://sm.config.inc/second.png"
    assert pick_image({"files": [[OK]]}) == OK                           # list in a list
    assert pick_image({"attachments": [{"file": {"url": OK}}]}) is None  # key "file" is not a URL key


def test_nested_dict_and_list_shapes():
    assert pick_image({"image": {"url": {"src": OK}}}) == OK
    assert pick_image({"images": [{"url": [{"href": OK}]}]}) == OK


def test_nesting_depth_is_limited():
    deep = OK
    for _ in range(10):
        deep = [deep]
    assert pick_image({"images": deep}) is None
    assert pick_image({"images": [[[OK]]]}) == OK


def test_nothing_usable_gives_none():
    assert pick_image({}) is None
    assert pick_image({"name": "Box", "icon": OK, "link": OK}) is None   # unknown keys are ignored
    assert pick_image({"image": None, "images": [], "files": {}}) is None
    assert pick_image({"image_url": "javascript:alert(1)"}) is None
    for junk in (None, "", "text", 5, [OK], ("x",), OK):
        assert pick_image(junk) is None


def test_pick_image_passes_base_and_hosts():
    row = {"image": "/a.png", "photo": "https://cdn.example.com/b.png"}
    assert pick_image(row, base="https://other.test") == "https://other.test/a.png"
    assert pick_image({"photo": "https://cdn.example.com/b.png"}) is None
    assert pick_image(row, hosts=("cdn.example.com",)) == "https://sm.config.inc/a.png"
    assert pick_image({"photo": "https://cdn.example.com/b.png"}, hosts=("example.com",)) \
        == "https://cdn.example.com/b.png"


# ----------------------------------------------------------------------------- hosts_from_env
def test_hosts_from_env_defaults():
    for empty in (None, "", "   ", ",", " , ,, "):
        assert hosts_from_env(empty) == images.DEFAULT_HOSTS == ("sm.config.inc", "configint-main.s3.amazonaws.com")


def test_hosts_from_env_parses_list():
    assert hosts_from_env("cdn.example.com") == ("cdn.example.com",)
    assert hosts_from_env(" SM.Config.INC , Cdn.Example.com ,, ") == ("sm.config.inc", "cdn.example.com")
    assert hosts_from_env("a.com,A.COM,b.org") == ("a.com", "b.org")     # duplicates dropped
    assert isinstance(hosts_from_env("a.com"), tuple)


# ----------------------------------------------------------------------------- detail_url
@pytest.mark.parametrize("object_id, expected", [
    ("42", "000042"),
    ("000042", "000042"),
    ("7", "000007"),
    ("123456", "123456"),
    ("OBJ-42", "000042"),                 # non-digits are dropped
    (" 4 2 ", "000042"),
    ("12-34", "001234"),
    ("1234567", "1234567"),               # longer ids are kept whole
    (42, "000042"),
    ("", "000000"),
    ("abc", "000000"),
    ("٤٢", "000000"),           # only ASCII digits count
])
def test_detail_url_padding(object_id, expected):
    assert detail_url(object_id) == f"https://sm.config.inc/object_detail/{expected}"


def test_detail_url_base():
    assert detail_url("5", base="https://other.test") == "https://other.test/object_detail/000005"
    assert detail_url("5", base="https://other.test/") == "https://other.test/object_detail/000005"
