import os
import pathlib
from stat import S_IREAD, S_IRGRP, S_IROTH
from unittest import mock

import hypothesis.strategies as st
import pytest
from drms import DrmsQueryError
from hypothesis import assume, given, settings
from parfive import Results
from parfive.utils import FailedDownload

import astropy.units as u

from sunpy import config
from sunpy.net import Fido, attr
from sunpy.net import attrs as a
from sunpy.net import jsoc
from sunpy.net.base_client import QueryResponseColumn, QueryResponseRow, QueryResponseTable
from sunpy.net.dataretriever.client import QueryResponse
from sunpy.net.fido_factory import UnifiedResponse
from sunpy.net.tests.strategies import goes_time, offline_instruments, online_instruments, srs_time, time_attr
from sunpy.net.vso import VSOQueryResponseTable
from sunpy.tests.helpers import no_vso, skip_jsoc, skip_windows
from sunpy.time import TimeRange, parse_time
from sunpy.util.exceptions import SunpyUserWarning

TIMEFORMAT = config.get("general", "time_format")


@st.composite
def offline_query(draw, instrument=offline_instruments()):
    """
    Strategy for any valid offline query
    """
    query = draw(instrument)
    # If we have AttrAnd then we don't have GOES
    if isinstance(query, a.Instrument) and query.value == 'goes':
        query &= draw(goes_time())
    else:
        query = attr.and_(query, draw(time_attr()))
    return query


@st.composite
def online_query(draw, instrument=online_instruments()):
    query = draw(instrument)

    if isinstance(query, a.Instrument) and query.value == 'eve':
        query &= a.Level.zero
    if isinstance(query, a.Instrument) and query.value == 'norh':
        query &= a.Wavelength(17*u.GHz)
    if isinstance(query, a.Instrument) and query.value == 'soon':
        query &= draw(srs_time())

    return query


@no_vso
@settings(deadline=50000, max_examples=10)
@given(offline_query())
def test_offline_fido(query):
    unifiedresp = Fido.search(query)
    check_response(query, unifiedresp)


@pytest.mark.remote_data
# Until we get more mocked, we can't really do this to online clients.
# TODO: Hypothesis this again
@pytest.mark.parametrize("query", [
    (a.Instrument.eve & a.Time('2014/7/7', '2014/7/14') & a.Level.zero),
    (a.Instrument.rhessi & a.Time('2014/7/7', '2014/7/14')),
    (a.Instrument.norh & a.Time('2014/7/7', '2014/7/14') & a.Wavelength(17*u.GHz)),
])
def test_online_fido(query):
    unifiedresp = Fido.search(query)
    check_response(query, unifiedresp)


def check_response(query, unifiedresp):
    """
    Common test for online or offline query
    """
    query_tr = None
    query_instr = None
    for at in query.attrs:
        if isinstance(at, a.Time):
            query_tr = TimeRange(at.start, at.end)
        elif isinstance(at, a.Instrument):
            query_instr = at.value
    if not query_tr:
        raise ValueError("No Time Specified")

    for block in unifiedresp:
        for res in block:
            assert query_instr.lower() == res['Instrument'].lower()


@pytest.mark.remote_data
def test_save_path(tmpdir):
    qr = Fido.search(a.Instrument.eve, a.Time("2016/10/01", "2016/10/02"), a.Level.zero)

    # Test when path is str
    files = Fido.fetch(qr, path=str(tmpdir / "{instrument}" / "{level}"))
    for f in files:
        assert str(tmpdir) in f
        assert f"EVE{os.path.sep}0" in f


@pytest.mark.remote_data
def test_save_path_pathlib(tmpdir):
    qr = Fido.search(a.Instrument.eve, a.Time("2016/10/01", "2016/10/02"), a.Level.zero)

    # Test when path is pathlib.Path
    target_dir = tmpdir.mkdir("down")
    path = pathlib.Path(target_dir, "{instrument}", "{level}")
    files = Fido.fetch(qr, path=path)
    for f in files:
        assert target_dir.strpath in f
        assert f"EVE{os.path.sep}0" in f


@pytest.mark.remote_data
def test_save_path_cwd(tmpdir):
    qr = Fido.search(a.Instrument.eve, a.Time("2016/10/01", "2016/10/02"), a.Level.zero)

    # Test when path is ./ for current working directory
    os.chdir(tmpdir)  # move into temp directory
    files = Fido.fetch(qr, path="./")
    for f in files:
        assert pathlib.Path.cwd().joinpath(f).exists()


@pytest.mark.remote_data
def test_unified_response():
    start = parse_time("2012/1/1")
    end = parse_time("2012/1/2")
    qr = Fido.search(a.Instrument.eve, a.Level.zero, a.Time(start, end))
    assert qr.file_num == 2
    strings = ['eve', 'SDO', start.strftime(TIMEFORMAT), end.strftime(TIMEFORMAT)]
    assert all(s in qr._repr_html_() for s in strings)

@skip_jsoc
@pytest.mark.remote_data
def test_no_match():
    with pytest.raises(DrmsQueryError):
        Fido.search(a.Time("2016/10/01", "2016/10/02"), a.jsoc.Series("bob"),
                    a.Sample(10*u.s))


def test_call_error():
    with pytest.raises(TypeError) as excinfo:
        Fido()
    # Explicitly test all this error message as it's a copy of the one in
    # Python core.
    assert "'UnifiedDownloaderFactory' object is not callable" in str(excinfo.value)


@pytest.mark.remote_data
def test_fetch():
    qr = Fido.search(a.Instrument.eve,
                     a.Time("2016/10/01", "2016/10/02"),
                     a.Level.zero)
    res = Fido.fetch(qr)
    assert isinstance(res, Results)


@pytest.mark.remote_data
def test_unifiedresponse_slicing():
    results = Fido.search(
        a.Time("2012/1/1", "2012/1/2"), a.Instrument.lyra)
    assert isinstance(results[0:2], UnifiedResponse)
    assert isinstance(results[0], QueryResponseTable)


@pytest.mark.remote_data
def test_unifiedresponse_slicing_reverse():
    results = Fido.search(
        a.Time("2012/1/1", "2012/1/2"), a.Instrument.lyra)
    assert isinstance(results[::-1], UnifiedResponse)
    assert len(results[::-1]) == len(results[::1])
    assert isinstance(results[0, ::-1], QueryResponseTable)
    assert all(results[0][::-1] == results[0, ::-1])


@mock.patch("sunpy.net.vso.vso.build_client", return_value=True)
def test_vso_unifiedresponse(mock_build_client):
    vrep = VSOQueryResponseTable()
    vrep.client = True
    uresp = UnifiedResponse(vrep)
    assert isinstance(uresp, UnifiedResponse)


@pytest.mark.remote_data
def test_responses():
    results = Fido.search(
        a.Time("2012/1/1", "2012/1/2"), a.Instrument.lyra)
    assert isinstance(results, UnifiedResponse)
    for resp in results:
        assert isinstance(resp, QueryResponse)


@pytest.mark.remote_data
def test_repr():
    results = Fido.search(
        a.Time("2012/1/1", "2012/1/2"), a.Instrument.lyra)
    rep = repr(results)
    rep = rep.split('\n')
    # 6 preamble lines, 2 table header rule, the results table data and two blank lines at the end
    assert len(rep) == 6 + 2 + len(results[0]) + 2


def filter_queries(queries):
    return attr.and_(queries) not in queries


@pytest.mark.remote_data
def test_path(tmp_path):
    results = Fido.search(
        a.Time("2022/1/1", "2022/1/1"), a.Instrument.aia)
    file = Fido.fetch(results, path=tmp_path / "{file}")
    assert file == [str(pathlib.Path(tmp_path, "aia.lev1.335A_2022_01_01T00_00_00.62Z.image_lev1.fits"))]


@pytest.mark.remote_data
@skip_windows
def test_path_read_only(tmp_path):
    results = Fido.search(
        a.Time("2012/1/1", "2012/1/1"), a.Instrument.lyra, a.Level.two
    )
    # chmod doesn't seem to work correctly on the windows CI
    os.chmod(tmp_path, S_IREAD | S_IRGRP | S_IROTH)
    # Check to see if it's actually read only before running the test
    if not os.access(tmp_path, os.W_OK):
        with pytest.raises(PermissionError):
            Fido.fetch(results, path=tmp_path / "{file}")


@no_vso
@settings(deadline=50000, max_examples=10)
@given(st.tuples(offline_query(), offline_query()).filter(filter_queries))
def test_fido_indexing(queries):
    query1, query2 = queries

    # This is a work around for an aberration where the filter was not catching
    # this.
    assume(query1.attrs[1].start != query2.attrs[1].start)

    res = Fido.search(query1 | query2, combine=False)
    assert len(res) == 2

    assert isinstance(res[1:], UnifiedResponse)
    assert len(res[1:]) == 1
    assert isinstance(res[0:1], UnifiedResponse)
    assert len(res[0:1]) == 1

    assert isinstance(res[1:, 0], UnifiedResponse)
    assert len(res[1:, 0]) == 1
    assert isinstance(res[0:1, 0], UnifiedResponse)
    assert len(res[0:1, 0]) == 1

    assert isinstance(res[0][0], QueryResponseRow)
    assert isinstance(res[1][0], QueryResponseRow)
    assert isinstance(res[1, 0:1], QueryResponseTable)

    aa = res[0, 0]
    assert isinstance(aa, QueryResponseRow)

    aa = res[0, 'Instrument']
    assert isinstance(aa, QueryResponseColumn)

    aa = res[:, 'Instrument']
    assert isinstance(aa, UnifiedResponse)
    for table in aa:
        assert len(table.columns) == 1

    aa = res[0, ('Instrument',)]
    assert isinstance(aa, QueryResponseTable)
    for table in aa:
        assert len(table.columns) == 1

    aa = res[:, 0]

    if(len(aa) == 1):
        print("here aa = res[:, 0]", aa)

    assert isinstance(aa, UnifiedResponse)
    assert len(aa) == 2
    assert len(aa[0]) == 1

    aa = res[0, :]
    assert isinstance(aa, QueryResponseTable)

    aa = res[0, 1:]
    assert isinstance(aa, QueryResponseTable)

    if len(res.keys()) == len(res):
        aa = res[res.keys()[0], 1:]
        assert isinstance(aa, QueryResponseTable)
        aa = res[res.keys()[0], 'Instrument']
        assert isinstance(aa, QueryResponseColumn)

    with pytest.raises(IndexError):
        res[0, 0, 0]

    with pytest.raises(IndexError):
        res["saldkal"]

    with pytest.raises(IndexError):
        res[1.0132]

    if isinstance(res, UnifiedResponse):
        assert len(res) != 1

@pytest.mark.remote_data
def test_combined_response_vso_time():
    results = Fido.search((a.Time('2020-01-01', '2020-01-01 00:00:10') | a.Time('2020-01-03', '2020-01-03 00:00:10')) &
                  a.Instrument('AIA'), combine=True)
    assert len(results) == 1
    assert isinstance(results[0], QueryResponseTable)

    t1 = TimeRange(results[-1][0]["Start Time"], results[-1][-1]["End Time"])
    t2 = TimeRange('2020-01-01', '2020-01-03 00:00:10')
    assert t1 == t2

@pytest.mark.remote_data
def test_combined_response_jsoc():
    # Combining attributes other than time
    results = Fido.search(a.Time('2014-01-01T00:00:00', '2014-01-01T01:00:00'),
            a.jsoc.Series('hmi.v_45s') | a.jsoc.Series('aia.lev1_euv_12s'), combine=True)
    print(results)
    assert len(results) == 1
    assert isinstance(results[0], QueryResponseTable)

    results = Fido.search((a.Time('2020-01-01', '2020-01-01 00:00:10') | a.Time('2020-01-03', '2020-01-03 00:00:10')),
                           a.jsoc.Series('hmi.m_45s'))
    assert len(results) == 1
    assert isinstance(results[0], QueryResponseTable)
    # Testing that the entire time range is covered
    max_time = None
    min_time = None
    for t in results['jsoc']['T_REC']:
        if max_time is None or t > max_time:
            max_time = t
        if min_time is None or t < min_time:
            min_time = t

    print(max_time, min_time)
    t1 = TimeRange(min_time, max_time)
    print("hi")
    t2 = TimeRange('2020-01-01 00:00:45', '2020-01-03 00:00:45')
    print(t1, t2)
    assert t1.start.iso == t2.start.iso
    assert t1.end.iso == t2.end.iso


@pytest.mark.remote_data
def test_combined_response_lyra():
    # To check if the dataretrivers are working correctly on combining the results
    results = Fido.search((a.Time('2020-01-01', '2020-01-01 23:59:59.999') | a.Time('2020-01-03', '2020-01-03 23:59:59.999')), a.Instrument.lyra)
    print(results)
    assert len(results) == 1
    assert isinstance(results[0], QueryResponseTable)


    # Testing that the entire time range is covered
    t1 = TimeRange(results[-1][0]["Start Time"], results[-1][-1]["End Time"])
    t2 = TimeRange('2020-01-01', '2020-01-03 23:59:59.999')
    print(t1, t2)
    t1 == t2

@pytest.mark.remote_data
def test_combine_attr():
    results = Fido.search((a.Time('2020-01-01', '2020-01-01 00:00:10') | a.Time('2020-01-03', '2020-01-03 00:00:10')) &
                  a.Instrument('AIA'), combine=True)
    assert len(results) == 1
    assert isinstance(results[0], QueryResponseTable)

    results = Fido.search((a.Time('2020-01-01', '2020-01-01 00:00:10') | a.Time('2020-01-03', '2020-01-03 00:00:10')) &
                  a.Instrument('AIA'))
    assert len(results) == 1
    assert isinstance(results[0], QueryResponseTable)

    results = Fido.search((a.Time('2020-01-01', '2020-01-01 00:00:10') | a.Time('2020-01-03', '2020-01-03 00:00:10')) &
                  a.Instrument('AIA'), combine=False)
    assert len(results) == 2
    assert isinstance(results[0], QueryResponseTable)



@no_vso
@settings(deadline=50000, max_examples=10)
@given(st.tuples(offline_query(), offline_query()).filter(filter_queries))
def test_fido_iter(queries):
    query1, query2 = queries

    # This is a work around for an aberration where the filter was not catching
    # this.
    assume(query1.attrs[1].start != query2.attrs[1].start)

    res = Fido.search(query1 | query2)

    for resp in res:
        assert isinstance(resp, QueryResponse)


@no_vso
@settings(deadline=50000, max_examples=10)
@given(offline_query())
def test_repr2(query):
    res = Fido.search(query)

    for rep_meth in (res.__repr__, res.__str__, res._repr_html_):
        if len(res) == 1:
            assert "Provider" in rep_meth()
            assert "Providers" not in rep_meth()

        else:
            assert "Provider" not in rep_meth()
            assert "Providers" in rep_meth()


@mock.patch("parfive.Downloader.download", return_value=Results(["/tmp/test"]))
def test_retry(mock_retry):
    """
    Test that you can use Fido.fetch to retry failed downloads.
    """
    res = Results()
    res.data.append("/this/worked.fits")

    err1 = FailedDownload("This is not a filename", "http://not.url/test", None)
    err2 = FailedDownload("This is not a filename2", "http://not.url/test2", None)
    res.errors.append(err1)
    res.errors.append(err2)

    mock_retry.return_value._errors += [err2]

    res2 = Fido.fetch(res, Results(["/this/also/worked.fits"]))

    assert res2 is not res

    # Assert that the result of retry ends up in the returned Results() object
    assert res2.data == ["/this/worked.fits", "/tmp/test", "/this/also/worked.fits", "/tmp/test"]
    assert res2.errors == [err2, err2]


def results_generator(dl):
    http = dl.http_queue
    ftp = dl.ftp_queue
    # Handle compatibility with parfive 1.0
    if not isinstance(dl.http_queue, list):
        http = list(dl.http_queue._queue)
        ftp = list(dl.ftp_queue._queue)

    outputs = []
    for url in http + ftp:
        outputs.append(pathlib.Path(url.keywords['url'].split("/")[-1]))

    return Results(outputs)


def test_downloader_type_error():
    with pytest.raises(TypeError):
        Fido.fetch([], downloader=Results())


def test_mixed_retry_error():
    with pytest.raises(TypeError):
        Fido.fetch([], Results())


@pytest.mark.remote_data
@mock.patch("sunpy.net.dataretriever.sources.goes.XRSClient.fetch",
            return_value=["hello"])
def test_client_fetch_wrong_type(mock_fetch):
    query = a.Time("2011/01/01", "2011/01/02") & a.Instrument.goes
    qr = Fido.search(query)
    with pytest.raises(TypeError):
        Fido.fetch(qr)


@skip_jsoc
@pytest.mark.remote_data
def test_vso_fetch_hmi(tmpdir):
    start_time = "2017-01-25"
    end_time = "2017-01-25T23:59:59"
    results = Fido.search(a.Time(start_time, end_time),
                          a.Instrument.hmi & a.Physobs.los_magnetic_field,
                          a.Sample(1 * u.minute))
    files = Fido.fetch(results[0, 0], path=tmpdir)
    assert len(files) == 1


def test_fido_no_time(mocker):
    jsoc_mock = mocker.patch("sunpy.net.jsoc.JSOCClient.search")
    jsoc_mock.return_value = jsoc.JSOCResponse()
    Fido.search(a.jsoc.Series("test"))
    jsoc_mock.assert_called_once()


@skip_jsoc
@pytest.mark.remote_data
def test_jsoc_missing_email():
    res = Fido.search(a.Time("2011/01/01", "2011/01/01 00:01"), a.jsoc.Series.aia_lev1_euv_12s)
    with pytest.raises(ValueError, match=r"A registered email is required to get data from JSOC.*"):
        Fido.fetch(res)


@skip_jsoc
@pytest.mark.remote_data
@pytest.mark.xdist_group(name="jsoc")
def test_slice_jsoc(jsoc_test_email):
    tstart = '2011/06/07 06:32:45'
    tend = '2011/06/07 06:33:15'
    res = Fido.search(a.Time(tstart, tend), a.jsoc.Series('hmi.M_45s'),
                      a.jsoc.Notify(jsoc_test_email))
    with pytest.warns(SunpyUserWarning, match="Downloading of sliced JSOC results is not supported."):
        Fido.fetch(res[0, 0])


def test_fido_repr():
    output = repr(Fido)
    assert output[:50] == '<sunpy.net.fido_factory.UnifiedDownloaderFactory o'


@skip_jsoc
@pytest.mark.xdist_group(name="jsoc")
@pytest.mark.remote_data
def test_fido_metadata_queries(jsoc_test_email):
    results = Fido.search(a.Time('2010/8/1 03:40', '2010/8/1 3:40:10'),
                          a.hek.FI | a.hek.FL & (a.hek.FL.PeakFlux > 1000) |
                          a.jsoc.Series('hmi.m_45s') & a.jsoc.Notify(jsoc_test_email))
    assert len(results['hek']) == 2
    assert isinstance(results['hek'], UnifiedResponse)
    assert isinstance(results['hek'][0], QueryResponseTable)
    assert len(results['hek'][1]) == 2
    assert results[::-1][0] is results['jsoc']
    assert isinstance(results['jsoc'], QueryResponseTable)
    assert results.keys() == ['hek', 'jsoc']


def test_path_format_keys():
    t1 = QueryResponseTable({'Start Time': ['2011/01/01', '2011/01/02'],
                             '!excite!': ['cat', 'rabbit'],
                             '01 wibble': ['parsnip', 'door']})
    assert t1.path_format_keys() == {'start_time', '_excite_', '01_wibble'}

    t2 = QueryResponseTable({'End Time': ['2011/01/01', '2011/01/02'],
                             '!excite!': ['cat', 'rabbit']})
    assert t2.path_format_keys() == {'_excite_', 'end_time'}
    # Need to pass combine=False otherwise combine=True will take union of the
    # columns for multiple tables which will not have the same keys
    # because path_format_keys() takes intersection of the keys in the tables
    unif = UnifiedResponse(t1, t2, combine=False)
    assert unif.path_format_keys() == {'_excite_'}


@pytest.mark.remote_data
def test_fido_stereo_extent_type():
    res = Fido.search(a.Time('2008/01/14', '2008/01/14 01:00:00'), a.Instrument.secchi, a.Source('STEREO_A'), a.ExtentType('CORONA'))
    assert len(res[0]) == 123
    assert not all(res[0].columns["Extent Type"] == "CORONA")
