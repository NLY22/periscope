from periscope.collection.platforms import kuaishou


def test_identifies_kuaishou_captcha_response_without_treating_it_as_empty_search():
    assert kuaishou._search_error({"data": {"result": 400002}}) == "captcha_required"
    assert kuaishou._search_error({"data": {"result": 1, "feeds": []}}) == ""
