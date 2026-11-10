package app.buddy

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.aggregate.AggregateMetric
import androidx.health.connect.client.request.AggregateGroupByPeriodRequest
import androidx.health.connect.client.time.TimeRangeFilter
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
 * active minutes, calories), read only with the owner's permission and sent only to their own Buddy server
 * (POST /api/health/sync). Nothing is sent anywhere else.
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
    )

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
        val out = JSONArray()
        if (metrics.isNotEmpty()) {
            val start = LocalDate.now().minusDays((days - 1).toLong()).atStartOfDay()
            val end = LocalDate.now().plusDays(1).atStartOfDay()
            val groups = client.aggregateGroupByPeriod(
                AggregateGroupByPeriodRequest(
                    metrics = metrics,
                    timeRangeFilter = TimeRangeFilter.between(start, end),
                    timeRangeSlicer = Period.ofDays(1),
                )
            )
            for (g in groups) {
                val r = g.result
                val day = JSONObject().put("day", g.startTime.toLocalDate().toString())
                r[StepsRecord.COUNT_TOTAL]?.let { day.put("steps", it) }
                r[SleepSessionRecord.SLEEP_DURATION_TOTAL]?.let { day.put("sleep_minutes", it.toMinutes()) }
                r[HeartRateRecord.BPM_AVG]?.let { day.put("avg_hr", it) }
                r[RestingHeartRateRecord.BPM_AVG]?.let { day.put("resting_hr", it) }
                r[ExerciseSessionRecord.EXERCISE_DURATION_TOTAL]?.let { day.put("active_minutes", it.toMinutes()) }
                r[TotalCaloriesBurnedRecord.ENERGY_TOTAL]?.let { day.put("calories", it.inKilocalories.toLong()) }
                if (day.length() > 1) out.put(day)
            }
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
