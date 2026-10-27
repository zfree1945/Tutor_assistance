"""Exercise actual CCv2 input, REST bridge and saving without external services."""
from playwright.sync_api import expect, sync_playwright
import pytest

from map_browser_support import ROOT, start_isolated_server, deny_external_requests
from test_map_browser import close_mobile_sidebar


WRAPPER = '''
import runpy
import streamlit as st
from tutor_screen import map_store
from tutor_screen.maps import AmapClient, GeoPoint, MapCredentials, MapAPIError
if not st.session_state.get('_test_configured'):
    map_store.save_settings(map_store.MapSettings(default_city='福州市', credentials=MapCredentials(web_key='SYNTHETIC')))
    st.session_state.page = '地图设置'
    st.session_state._test_configured = True
    st.session_state.search_calls = []
def search(self, query, city=''):
    st.session_state.search_calls.append(query)
    if query == '无结果': return []
    if query == '查询失败': raise MapAPIError('合成查询失败，请换词重试。')
    return [GeoPoint(lng=119.2, lat=26.0, label=query + '(旗山校区) · 合成地址1号', city=city),
            GeoPoint(lng=119.3, lat=26.1, label=query + '(屏山校区) · 合成地址2号', city=city)]
AmapClient.search = search
runpy.run_path('app.py', run_name='__main__')
st.caption('查询次数：' + str(len(st.session_state.search_calls)))
'''


@pytest.mark.parametrize('width', [390, 1280])
def test_origin_type_suggest_choose_save_and_invalidate(tmp_path, width):
    _, start, stop, log = start_isolated_server(tmp_path, WRAPPER)
    try:
        url = start()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': width, 'height': 900})
            deny_external_requests(context)
            page = context.new_page()
            page.goto(url)
            query = page.get_by_role('combobox', name='搜索出发点')
            expect(query).to_be_visible()
            if width < 600:
                close_mobile_sidebar(page)
            name = page.get_by_label('出发点名称', exact=True)
            name_label = page.locator('.st-key-map_origin_name [data-testid="stWidgetLabel"]')
            query_label = page.locator('.origin-search label')
            name_box = page.locator('.st-key-map_origin_name [data-testid="stTextInputRootElement"]')
            for left, right in [(name_label, query_label), (name_box, query)]:
                a, b = left.bounding_box(), right.bounding_box()
                assert abs(a['height'] - b['height']) <= 1
                assert abs(a['width'] - b['width']) <= 1
                if width >= 600:
                    assert abs(a['y'] - b['y']) <= 1
            for left, right in [(name_label, query_label), (name, query)]:
                for prop in ['fontSize', 'lineHeight', 'fontFamily']:
                    assert left.evaluate('(e, p) => getComputedStyle(e)[p]', prop) == right.evaluate('(e, p) => getComputedStyle(e)[p]', prop)
            expect(page.get_by_role('button', name='搜索出发点', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='在地图选择出发点')).to_have_count(0)
            expect(page.get_by_text('出发点候选', exact=True)).to_have_count(0)
            page.get_by_label('出发点名称', exact=True).fill('学校')
            query.fill('福建')
            query.fill('福建中医药大学')
            options = page.get_by_role('option')
            expect(options).to_have_count(2)
            expect(page.get_by_text('查询次数：1', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='保存常用出发点')).to_have_count(0)
            folder = ROOT / 'artifacts' / 'acceptance' / 'origin_search'
            folder.mkdir(parents=True, exist_ok=True)
            options.last.scroll_into_view_if_needed()
            expect(options.first).to_be_visible()
            page.screenshot(path=str(folder / f'suggestions_{width}.png'), full_page=True)
            query.press('ArrowDown')
            query.press('ArrowDown')
            query.press('Enter')
            expect(query).to_have_value('福建中医药大学(屏山校区) · 合成地址2号 · 福州市')
            save = page.get_by_role('button', name='保存常用出发点')
            expect(save).to_be_visible()
            save.click()
            saved_label = '学校 · 福建中医药大学(屏山校区) · 合成地址2号 · 福州市'
            expect(page.get_by_text(saved_label, exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_text(saved_label, exact=True)).to_be_visible()
            query = page.get_by_role('combobox', name='搜索出发点')
            if width < 600:
                close_mobile_sidebar(page)
            page.get_by_label('出发点名称', exact=True).fill('住处')
            page.get_by_text(saved_label, exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(folder / f'saved_address_{width}.png'), full_page=True)
            query.fill('新地点')
            expect(options).to_have_count(2)
            options.first.click()
            expect(save).to_be_visible()
            query.fill('无结果')
            expect(page.get_by_text('没有找到地点，请换个关键词。', exact=True)).to_be_visible()
            expect(save).to_have_count(0)
            query.fill('查询失败')
            expect(page.get_by_text('合成查询失败，请换词重试。', exact=True)).to_be_visible()
            expect(save).to_have_count(0)
            query.fill('')
            expect(options).to_have_count(0)
            expect(save).to_have_count(0)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            context.close()
            browser.close()
    finally:
        stop()
        log.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_city_mismatch_warning_is_red_below_city_and_matches_label_size(tmp_path, width):
    wrapper = WRAPPER.replace("default_city='福州市'", "default_city='温州市'").replace('city=city)', "city='福州市')")
    _, start, stop, log = start_isolated_server(tmp_path, wrapper)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': width, 'height': 900})
            deny_external_requests(context)
            page = context.new_page()
            page.goto(start())
            query = page.get_by_role('combobox', name='搜索出发点')
            expect(query).to_be_visible()
            if width < 600:
                close_mobile_sidebar(page)
            query.fill('合成学校')
            page.get_by_role('option').first.click()
            warning = page.locator('.origin-city-warning')
            expect(warning).to_contain_text('请重新填写默认城市')
            expect(warning).to_have_css('color', 'rgb(198, 40, 40)')
            label = page.locator('.st-key-map_default_city label p')
            assert warning.evaluate('(e)=>getComputedStyle(e).fontSize') == label.evaluate('(e)=>getComputedStyle(e).fontSize')
            city = page.get_by_label('默认城市', exact=True)
            a, b = city.bounding_box(), warning.bounding_box()
            assert 0 <= b['y'] - (a['y'] + a['height']) < 30
            assert abs(a['x'] - b['x']) < 2
            expect(page.get_by_role('button', name='保存常用出发点')).to_be_disabled()
            warning.scroll_into_view_if_needed()
            folder = ROOT / 'artifacts' / 'acceptance' / 'origin_search'
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f'city_warning_{width}.png'), full_page=True)
            city.fill('福州市')
            city.press('Tab')
            expect(warning).to_contain_text('请先保存地图设置')
            page.get_by_role('button', name='保存地图设置', exact=True).click()
            expect(warning).to_have_count(0)
            query.fill('新学校')
            page.get_by_role('option').first.click()
            expect(warning).to_have_count(0)
            expect(page.get_by_role('button', name='保存常用出发点')).to_be_enabled()
            query.fill('')
            expect(warning).to_have_count(0)
            context.close()
            browser.close()
    finally:
        stop()
        log.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_import_origin_reminder_and_detailed_options(tmp_path, width):
    wrapper = '''
import runpy
import streamlit as st
from tutor_screen import map_store
from tutor_screen.maps import GeoPoint, MapCredentials
if not st.session_state.get('_test_configured'):
    map_store.save_settings(map_store.MapSettings(default_city='福州市', credentials=MapCredentials(web_key='SYNTHETIC')))
    map_store.save_origin('学校', GeoPoint(lng=119.3, lat=26.1, label='合成学校 · 合成路1号', city='福州市'))
    st.session_state.page = '订单导入'
    st.session_state._test_configured = True
runpy.run_path('app.py', run_name='__main__')
'''
    _, start, stop, log = start_isolated_server(tmp_path, wrapper)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': width, 'height': 900})
            deny_external_requests(context)
            page = context.new_page()
            page.goto(start())
            field = page.locator('.st-key-map_origin_draft')
            expect(field).to_be_visible()
            if width < 600:
                close_mobile_sidebar(page)
            warning = page.locator('.origin-selection-warning')
            expect(warning).to_contain_text('请选择出发点')
            expect(warning).to_have_css('color', 'rgb(198, 40, 40)')
            a = field.locator('[data-testid="stSelectbox"]').bounding_box()
            b = warning.bounding_box()
            assert 0 <= b['y'] - (a['y'] + a['height']) < 30
            assert abs(a['x'] - b['x']) < 2
            field.scroll_into_view_if_needed()
            field.get_by_role('button', name='Open', exact=True).click()
            label = '学校 · 合成学校 · 合成路1号 · 福州市'
            option = page.get_by_role('option', name=label, exact=True)
            expect(option).to_be_visible()
            folder = ROOT / 'artifacts' / 'acceptance' / 'origin_search'
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f'import_origin_{width}.png'), full_page=True)
            option.click()
            expect(field.get_by_role('combobox')).to_have_value(label)
            expect(warning).to_have_count(0)
            field.get_by_role('button', name='Open', exact=True).click()
            page.get_by_role('option', name='未设置出发点', exact=True).click()
            expect(warning).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            context.close()
            browser.close()
    finally:
        stop()
        log.close()
