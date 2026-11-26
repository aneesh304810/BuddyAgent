package app.buddy

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.BloodGlucoseRecord
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RespiratoryRateRecord
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.aggregate.AggregateMetric
import androidx.health.connect.client.request.AggregateGroupByPeriodRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Instant
import java.time.ZoneId
import kotlin.reflect.KClass
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.Period
import java.util.concurrent.TimeUnit

/**
 * Buddy Connect's Health group, phone side: daily totals from Google Health Connect (steps, sleep, heart rate,
 * active minutes, calories) and the day's HRV, blood oxygen, weight, body fat, blood pressure, glucose, breathing
 * rate and VO2 max (whatever Garmin Connect, Fitbit and the Pixel Watch, Samsung Health, a scale, a cuff or a meter
 * wrote there), with the apps that wrote each day. Read only with the owner's permission, type by type, and sent
 * only to their own Buddy server (POST /api/health/sync). Nothing is sent anywhere else.
 */
object HealthSync {
    private val http = OkHttpClient.Builder().callTimeout(60, TimeUnit.SECONDS).build()
    private const val PREFS = "buddy_health"
    private const val EVERY_MS = 6 * 60 * 60 * 1000L  // sync at most every 6 hours when the app opens

    val PERMISSIONS: Set<String> = setOf(
        HealthPermission.getReadPermission(StepsRecord::class),
        HealthPermission.getReadPermission(SleepSessionRecord::class),
        HealthPermission.getReadPermission(HeartRateRecord::class),
        HealthPermission.getReadPermission(RestingHeartRateRecord::class),
        HealthPermission.getReadPermission(ExerciseSessionRecord::class),
        HealthPermission.getReadPermission(TotalCaloriesBurnedRecord::class),
        HealthPermission.getReadPermission(HeartRateVariabilityRmssdRecord::class),
        HealthPermission.getReadPermission(OxygenSaturationRecord::class),
        HealthPermission.getReadPermission(WeightRecord::class),
        HealthPermission.getReadPermission(BodyFatRecord::class),
        HealthPermission.getReadPermission(BloodPressureRecord::class),
        HealthPermission.getReadPermission(BloodGlucoseRecord::class),
        HealthPermission.getReadPermission(RespiratoryRateRecord::class),
        HealthPermission.getReadPermission(Vo2MaxRecord::class),
    )

    /** Every record of one type in a time range, page by page (at most 20,000). */
    private suspend fun <T : Record> readAll(
        client: HealthConnectClient, type: KClass<T>, start: Instant, end: Instant,
    ): List<T> {
        val out = mutableListOf<T>()
        var token: String? = null
        do {
            val page = client.readRecords(
                ReadRecordsRequest(recordType = type, timeRangeFilter = TimeRangeFilter.between(start, end), pageToken = token)
            )
            out += page.records
            token = page.pageToken
        } while (token != null && out.size < 20_000)
        return out
    }

    private fun dayOf(time: Instant): String = time.atZone(ZoneId.systemDefault()).toLocalDate().toString()

    fun available(context: Context): Boolean =
        HealthConnectClient.getSdkStatus(context) == HealthConnectClient.SDK_AVAILABLE

    suspend fun granted(context: Context): Set<String> =
        if (!available(context)) emptySet()
        else HealthConnectClient.getOrCreate(context).permissionController.getGrantedPermissions()
            .intersect(PERMISSIONS)

    /** The last [days] days, one total per day, only for what the owner allowed. */
    suspend fun collect(context: Context, days: Int = 30): JSONObject {
        val client = HealthConnectClient.getOrCreate(context)
        val allowed = granted(context)
        val metrics = mutableSetOf<AggregateMetric<*>>()
        if (HealthPermission.getReadPermission(StepsRecord::class) in allowed) metrics.add(StepsRecord.COUNT_TOTAL)
        if (HealthPermission.getReadPermission(SleepSessionRecord::class) in allowed)
            metrics.add(SleepSessionRecord.SLEEP_DURATION_TOTAL)
        if (HealthPermission.getReadPermission(HeartRateRecord::class) in allowed) metrics.add(HeartRateRecord.BPM_AVG)
        if (HealthPermission.getReadPermission(RestingHeartRateRecord::class) in allowed)
            metrics.add(RestingHeartRateRecord.BPM_AVG)
        if (HealthPermission.getReadPermission(ExerciseSessionRecord::class) in allowed)
            metrics.add(ExerciseSessionRecord.EXERCISE_DURATION_TOTAL)
        if (HealthPermission.getReadPermission(TotalCaloriesBurnedRecord::class) in allowed)
            metrics.add(TotalCaloriesBurnedRecord.ENERGY_TOTAL)
        val byDay = linkedMapOf<String, JSONObject>()
        val apps = mutableMapOf<String, MutableSet<String>>()
        fun day(d: String): JSONObject = byDay.getOrPut(d) { JSONObject().put("day", d) }
        fun origin(d: String, packageName: String) { apps.getOrPut(d) { mutableSetOf() }.add(packageName) }
        val start = LocalDate.now().minusDays((days - 1).toLong()).atStartOfDay()
        val end = LocalDate.now().plusDays(1).atStartOfDay()
        if (metrics.isNotEmpty()) {
            val groups = client.aggregateGroupByPeriod(
                AggregateGroupByPeriodRequest(
                    metrics = metrics,
                    timeRangeFilter = TimeRangeFilter.between(start, end),
                    timeRangeSlicer = Period.ofDays(1),
                )
            )
            for (g in groups) {
                val r = g.result
                val d = g.startTime.toLocalDate().toString()
                val row = JSONObject()
                r[StepsRecord.COUNT_TOTAL]?.let { row.put("steps", it) }
                r[SleepSessionRecord.SLEEP_DURATION_TOTAL]?.let { row.put("sleep_minutes", it.toMinutes()) }
                r[HeartRateRecord.BPM_AVG]?.let { row.put("avg_hr", it) }
                r[RestingHeartRateRecord.BPM_AVG]?.let { row.put("resting_hr", it) }
                r[ExerciseSessionRecord.EXERCISE_DURATION_TOTAL]?.let { row.put("active_minutes", it.toMinutes()) }
                r[TotalCaloriesBurnedRecord.ENERGY_TOTAL]?.let { row.put("calories", it.inKilocalories.toLong()) }
                if (row.length() > 0) {
                    val target = day(d)
                    row.keys().forEach { k -> target.put(k, row.get(k)) }
                    r.dataOrigins.forEach { origin(d, it.packageName) }
                }
            }
        }
        // Readings taken at a moment: the day's average, or for weight, body fat, blood pressure and VO2 max the
        // day's last one. Each type only when the owner allowed it.
        val from = start.atZone(ZoneId.systemDefault()).toInstant()
        val to = end.atZone(ZoneId.systemDefault()).toInstant()
        fun average(key: String, values: List<Pair<Instant, Double>>) =
            values.groupBy { dayOf(it.first) }.forEach { (d, v) -> day(d).put(key, v.map { it.second }.average()) }
        fun latest(key: String, values: List<Pair<Instant, Double>>) =
            values.groupBy { dayOf(it.first) }.forEach { (d, v) -> day(d).put(key, v.maxByOrNull { it.first }!!.second) }
        suspend fun <T : Record> allowedRecords(type: KClass<T>): List<T> =
            if (HealthPermission.getReadPermission(type) in allowed) runCatching { readAll(client, type, from, to) }
                .getOrDefault(emptyList()) else emptyList()
        allowedRecords(HeartRateVariabilityRmssdRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            average("hrv", list.map { it.time to it.heartRateVariabilityMillis })
        }
        allowedRecords(OxygenSaturationRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            average("spo2", list.map { it.time to it.percentage.value })
        }
        allowedRecords(RespiratoryRateRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            average("respiratory_rate", list.map { it.time to it.rate })
        }
        allowedRecords(BloodGlucoseRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            average("glucose_mgdl", list.map { it.time to it.level.inMilligramsPerDeciliter })
        }
        allowedRecords(WeightRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            latest("weight_kg", list.map { it.time to it.weight.inKilograms })
        }
        allowedRecords(BodyFatRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            latest("body_fat", list.map { it.time to it.percentage.value })
        }
        allowedRecords(BloodPressureRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            latest("bp_systolic", list.map { it.time to it.systolic.inMillimetersOfMercury })
            latest("bp_diastolic", list.map { it.time to it.diastolic.inMillimetersOfMercury })
        }
        allowedRecords(Vo2MaxRecord::class).also { list ->
            list.forEach { origin(dayOf(it.time), it.metadata.dataOrigin.packageName) }
            latest("vo2max", list.map { it.time to it.vo2MillilitersPerMinuteKilogram })
        }
        val out = JSONArray()
        for ((d, row) in byDay.toSortedMap()) {
            apps[d]?.let { row.put("apps", JSONArray(it.take(12))) }
            if (row.length() > 1) out.put(row)
        }
        return JSONObject().put("days", out)
    }

    /** Collect and send to the owner's server. Returns how many days were sent. */
    suspend fun sync(context: Context, link: ServerLink, days: Int = 30): Int = withContext(Dispatchers.IO) {
        val body = collect(context, days)
        val request = Request.Builder()
            .url(link.baseUrl + "/api/health/sync")
            .header("Authorization", "Bearer ${link.token}")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("server said ${response.code}")
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putLong("synced", System.currentTimeMillis()).apply()
        body.getJSONArray("days").length()
    }

    /** When the app opens: sync if allowed and the last sync is over 6 hours old (quietly; errors are skipped). */
    suspend fun maybeSync(context: Context, link: ServerLink?) {
        if (link == null || !available(context)) return
        val last = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong("synced", 0)
        if (System.currentTimeMillis() - last < EVERY_MS) return
        runCatching { if (granted(context).isNotEmpty()) sync(context, link, 7) }
    }
}
