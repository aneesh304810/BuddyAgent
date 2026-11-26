package app.buddy

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The redesign's five destinations (migration step 4): every screen still has a home, in both layouts. */
class NavTest {
    private val all = listOf(
        Screen.Talk, Screen.Settings, Screen.Reports, Screen.Insights, Screen.Library, Screen.Dashboard, Screen.News,
        Screen.Investments, Screen.Deals, Screen.Alerts, Screen.Security, Screen.Health, Screen.Intelligence,
        Screen.Maestro, Screen.Trips, Screen.Discover, Screen.More, Screen.Travel, Screen.Connections, Screen.Explore,
        Screen.Profile, Screen.Email, Screen.LibraryItem("x"), Screen.Card("{}"), Screen.Report(1),
    )

    @After
    fun backToTheDefault() {
        NavLayout.v2 = true
    }

    @Test
    fun everyScreenLeadsBackToOneOfTheFiveInEitherLayout() {
        for (v2 in listOf(true, false)) {
            NavLayout.v2 = v2
            for (screen in all) {
                var at: Screen = screen
                var steps = 0
                while (true) {
                    at = parentOf(at) ?: break
                    steps++
                    assertTrue("Back from $screen goes round in a loop", steps < 5)
                }
                assertTrue("$screen (v2=$v2) is under ${placeOf(screen)}", placeOf(screen) in places())
            }
            places().forEach { assertEquals(it, placeOf(homeOf(it))) }
        }
    }

    @Test
    fun theRedesignsDestinations() {
        NavLayout.v2 = true
        assertEquals(listOf("Home", "Health", "Money", "Explore", "Profile"), places().map { it.label })
        assertEquals(Place.HEALTH, placeOf(Screen.Health))
        assertEquals(Place.EXPLORE, placeOf(Screen.Deals))
        assertEquals(Place.EXPLORE, placeOf(Screen.Report(1)))
        assertEquals(Place.PROFILE, placeOf(Screen.Email))
        assertEquals(Place.PROFILE, placeOf(Screen.Maestro))
        assertEquals(Place.HOME, placeOf(Screen.Alerts))
        assertEquals(Screen.Profile, settingsParent())
    }

    @Test
    fun quickActionsOpenTheirSection() {
        NavLayout.v2 = true
        val opened = Quick.entries.associateWith { q -> var to: Screen? = null; openQuick(q) { to = it }; to }
        assertEquals(Screen.Health, opened[Quick.BP])
        assertEquals(Screen.Dashboard, opened[Quick.SPENDING])
        assertEquals(Screen.Explore, opened[Quick.DEALS])
        assertEquals("scan", Jump.health)  // the last Health one asked to open the scanner
        assertEquals(1, Jump.explore)
        Jump.health = null; Jump.money = null; Jump.explore = null
    }

    @Test
    fun theEarlierPlacesStayBehindTheSwitch() {
        NavLayout.v2 = false
        assertEquals(listOf("Buddy", "Money", "Trips & Deals", "Discover", "More"), places().map { it.label })
        assertEquals(Place.MORE, placeOf(Screen.Health))
        assertEquals(Screen.More, settingsParent())
    }
}
