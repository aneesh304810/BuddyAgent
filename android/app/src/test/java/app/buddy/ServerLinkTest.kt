package app.buddy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerLinkTest {
    @Test
    fun readsTheLinkTheSetupScriptPrints() {
        val link = ServerLink.parse("https://Buddy.tail1234.ts.net/?token=abc%2Bdef")!!
        assertEquals("https://buddy.tail1234.ts.net", link.baseUrl)
        assertEquals("abc+def", link.token)
        assertEquals("wss://buddy.tail1234.ts.net/ws?token=abc%2Bdef", link.socketUrl)
    }

    @Test
    fun keepsAPort() {
        assertEquals("https://host:8443", ServerLink.parse("https://host:8443/?token=t")!!.baseUrl)
    }

    @Test
    fun usesTheSavedTokenWhenTheLinkHasNone() {
        assertEquals("saved", ServerLink.parse("https://host/", fallbackToken = "saved")!!.token)
        assertEquals("new", ServerLink.parse("https://host/?token=new", fallbackToken = "saved")!!.token)
    }

    @Test
    fun refusesUnencryptedOrIncompleteLinks() {
        assertNull(ServerLink.parse("http://host/?token=t"))
        assertNull(ServerLink.parse("https://host/"))
        assertNull(ServerLink.parse("not a link"))
        assertNull(ServerLink.parse("https://host/?token="))
    }
}
