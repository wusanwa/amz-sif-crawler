from amz_sif_crawler.fetchers.sif import detect_sif_auth_state


def test_detect_sif_auth_state_treats_marketing_homepage_as_login_required():
    text = (
        "功能介绍 生态中心 关于我们 帮助中心 API数据服务 会员购买 免费使用插件 "
        "注册免费领会员 登录 40W+ 亚马逊卖家在用的关键词运营工具"
    )
    assert detect_sif_auth_state("https://www.sif.com/", text) == "login_required"


def test_detect_sif_auth_state_recognizes_reverse_page_as_ok():
    text = "查流量结构 反查流量词 广告透视仪 流量时光机"
    assert (
        detect_sif_auth_state(
            "https://www.sif.com/reverse?country=US&asin=B0CDX5XGLK&isListingSearch=false&trafficType=",
            text,
        )
        == "ok"
    )
