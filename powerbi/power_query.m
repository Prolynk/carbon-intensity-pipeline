// Power Query (M) for the three model tables.
// Home > Transform data > Advanced Editor, one query per table.
//
// Two source options:
//   A. CSV exports in powerbi/model_data/ -- no driver needed, shown below.
//   B. The SQLite file directly, via Odbc.DataSource with the SQLite ODBC driver,
//      shown at the bottom. Option A is used for the committed .pbix so the file
//      opens on any machine.
//
// Set the parameter RepoPath to the folder containing this repository.

// ---------------------------------------------------------------- parameter
RepoPath = "C:\Users\you\carbon-intensity-pipeline";


// ----------------------------------------------------------------- DimDate
let
    Source = Csv.Document(
        File.Contents(RepoPath & "\powerbi\model_data\dim_date.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(
        Promoted,
        {
            {"date_key", Int64.Type},
            {"full_date", type date},
            {"year", Int64.Type},
            {"quarter", Int64.Type},
            {"month_number", Int64.Type},
            {"month_name", type text},
            {"day_of_month", Int64.Type},
            {"weekday_number", Int64.Type},
            {"weekday_name", type text},
            {"is_weekend", Int64.Type},
            {"iso_week", Int64.Type}
        }
    ),
    Weekend = Table.TransformColumns(Typed, {{"is_weekend", each _ = 1, type logical}}),
    Renamed = Table.RenameColumns(
        Weekend,
        {
            {"date_key", "DateKey"}, {"full_date", "FullDate"}, {"year", "Year"},
            {"quarter", "Quarter"}, {"month_number", "MonthNumber"}, {"month_name", "MonthName"},
            {"day_of_month", "DayOfMonth"}, {"weekday_number", "WeekdayNumber"},
            {"weekday_name", "WeekdayName"}, {"is_weekend", "IsWeekend"}, {"iso_week", "IsoWeek"}
        }
    )
in
    Renamed
// Then: Table tools > Mark as date table > FullDate.
// And: Column tools > sort MonthName by MonthNumber, WeekdayName by WeekdayNumber.


// ----------------------------------------------------------------- DimTime
let
    Source = Csv.Document(
        File.Contents(RepoPath & "\powerbi\model_data\dim_time.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(
        Promoted,
        {
            {"time_key", Int64.Type}, {"time_label", type text},
            {"hour_of_day", Int64.Type}, {"half_hour_of_hour", Int64.Type},
            {"day_part", type text}
        }
    ),
    Renamed = Table.RenameColumns(
        Typed,
        {
            {"time_key", "TimeKey"}, {"time_label", "TimeLabel"}, {"hour_of_day", "HourOfDay"},
            {"half_hour_of_hour", "HalfHourOfHour"}, {"day_part", "DayPart"}
        }
    )
in
    Renamed
// Then: sort TimeLabel by TimeKey, and DayPart by a custom order column if you
// want the day parts to read Night -> Morning -> Afternoon -> Evening.


// ----------------------------------------------------------- FactIntensity
let
    Source = Csv.Document(
        File.Contents(RepoPath & "\powerbi\model_data\fact_intensity.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(
        Promoted,
        {
            {"period_start_utc", type datetime},
            {"period_end_utc", type datetime},
            {"period_start_local", type datetime},
            {"date_key", Int64.Type},
            {"time_key", Int64.Type},
            {"forecast", type number},
            {"actual", type number},
            {"intensity_index", type text},
            {"forecast_error", type number},
            {"abs_forecast_error", type number},
            {"has_actual", Int64.Type},
            {"loaded_at_utc", type datetime}
        }
    ),
    // Drop the derived error columns: they are recomputed in DAX so the model has
    // one definition of the error, not two that can drift apart.
    Trimmed = Table.RemoveColumns(Typed, {"forecast_error", "abs_forecast_error", "loaded_at_utc"})
in
    Trimmed


// ------------------------------------------------- DataQuality (page 4 source)
let
    Source = Csv.Document(
        File.Contents(RepoPath & "\powerbi\model_data\data_quality_results.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(
        Promoted,
        {
            {"run_id", type text}, {"run_at_utc", type datetime}, {"check_name", type text},
            {"passed", Int64.Type}, {"severity", type text},
            {"issue_count", Int64.Type}, {"detail", type text}
        }
    ),
    LatestRun = Table.SelectRows(Typed, each [run_at_utc] = List.Max(Typed[run_at_utc]))
in
    LatestRun


// ------------------------------------------------------ Option B: live SQLite
// Requires the SQLite ODBC driver (http://www.ch-werner.de/sqliteodbc/) and a DSN.
// let
//     Source = Odbc.DataSource("dsn=CarbonIntensity", [HierarchicalNavigation = true]),
//     Db = Source{[Name = "main"]}[Data],
//     Fact = Db{[Name = "fact_intensity"]}[Data]
// in
//     Fact
