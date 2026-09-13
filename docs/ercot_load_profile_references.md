# ERCOT Load Profile References

## Primary ERCOT pages

- [Backcasted (Actual) Load Profiles - Historical](https://www.ercot.com/mktinfo/loadprofile/alp)
- [Load Profiling Guide](https://www.ercot.com/mktrules/guides/loadprofiling)
- [Load Profiling Guide Library](https://www.ercot.com/mktrules/guides/loadprofiling/library)
- [ERS Forms & Supporting Documents](https://www.ercot.com/services/programs/load/eils/documents)

The historical backcast page describes the downloads as 15-minute kWh values
for all profile types and weather zones. This supports the source contract used
by Phase 3.

## ERCOT interval-format examples

- [Normal Excel data-column example](https://www.ercot.com/files/docs/2011/05/05/excel_data_column_format_example.xls)
- [Fall DST Excel data-column example](https://www.ercot.com/files/docs/2020/12/15/Excel_Data_Column_format_Fall_dst_example.xls)
- [Spring DST Excel data-column example](https://www.ercot.com/files/docs/2020/12/15/Excel_Data_Column_format_Spring_dst_example.xlsx)

The examples use `Date`, `Time`, and `kWh` fields. Their observed behavior is:

- Normal day: 96 quarter-hour records.
- Spring-forward day: 92 records; the sequence skips `02:00`, `02:15`,
  `02:30`, and `02:45`.
- Fall-back day: 100 quarter-hour records across the repeated hour.

These examples establish that the rare `int_kWh97` through `int_kWh100`
values in the backcast workbooks are legitimate fall-DST observations, not
invalid or arbitrary extra columns.

## Phase 3 implications

The normalized profile should:

- retain all source intervals 1 through 100;
- preserve `Date`, `ADDTIME`, and the source interval number;
- treat values as kWh per 15-minute interval;
- preserve repeated fall-back observations rather than deduplicating them;
- represent spring-forward days with missing local intervals;
- defer canonical UTC conversion until the exact `Date`/`ADDTIME` convention
  in the backcast files is verified.

The source examples establish interval duration and DST cardinality, but they
do not by themselves prove the exact meaning of `ADDTIME` in the backcast
workbooks. That remains a targeted Phase 3 validation item.

## Evidence policy

These links are the provenance for the interpretation. The raw ERCOT workbook
files remain in `data/raw/ercot_backcast/` and are not rewritten. Any future
change to the interval or timestamp contract should cite an ERCOT source and
add a fixture test for the affected calendar case.
