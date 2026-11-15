package com.junction.glasses.live

import com.junction.glasses.core.HttpTrace
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.TimeUnit

/** The wearer's 3D view: the WebView's requests reach the relay with the key in a header, never in a URL. */
class RelayPageProxyTest {
    private val key = "synthetic-relay-key"

    @Test fun pageRequestsGoToTheRelayWithTheKeyHeaderAndThePrefixRemoved() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setHeader("Content-Type", "text/html; charset=utf-8").setBody("<html>room</html>"))
            server.enqueue(MockResponse().setHeader("Content-Type", "application/octet-stream").setBody("splat"))
            server.start()
            HttpTrace.clear()
            val proxy = RelayPageProxy({ RelayPageProxy.Target(listOf(server.url("/")), key) })
            val page = proxy.handle("GET", "/relay/care", "zone=kitchen&view=wearer")
            assertEquals(200, page.status)
            assertEquals("text/html", page.mimeType)
            assertEquals("utf-8", page.encoding)
            assertEquals("<html>room</html>", page.body.readBytes().toString(Charsets.UTF_8))
            val first = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/care?zone=kitchen&view=wearer", first.path)
            assertEquals(key, first.getHeader("X-Key"))
            val splat = proxy.handle("GET", "/relay/evidence/train-kitchen-1/trained.splat", null)
            assertEquals("splat", splat.body.readBytes().toString(Charsets.UTF_8))
            assertEquals("/evidence/train-kitchen-1/trained.splat", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            for ((label, _) in HttpTrace.snapshot()) assertFalse(label.contains(key) || label.contains("?"))
        }
    }

    @Test fun onlyTheReadOnlyRoutesThePageUsesAreForwarded() {
        MockWebServer().use { server ->
            server.start()
            val proxy = RelayPageProxy({ RelayPageProxy.Target(listOf(server.url("/")), key) })
            assertEquals(405, proxy.handle("POST", "/relay/locate", "zone=kitchen").status)
            for ((path, query) in listOf(
                "/relay/locate" to "zone=kitchen", "/relay/waypoint" to "zone=kitchen&name=x&frame=1&u=0.5&v=0.5",
                "/relay/recording" to "zone=kitchen", "/relay/navigate" to "zone=kitchen&to=pill+box",
                "/relay/care" to "zone=kitchen&k=$key", "/relay/where" to "zone=kitchen&k=$key", "/relay/care" to null,
                "/care" to "zone=kitchen", "/relay/../care" to "zone=kitchen", "/relay/evidence/../../etc/passwd" to null,
            )) {
                assertEquals("$path?$query", 404, proxy.handle("GET", path, query).status)
            }
            assertEquals("nothing reached the relay", 0, server.requestCount)
            assertTrue(RelayPageProxy.isAllowed("/navigate", "zone=kitchen"))
            assertTrue(RelayPageProxy.isAllowed("/find", "zone=kitchen&what=orange%20box"))
            assertTrue(RelayPageProxy.isAllowed("/me", "zone=kitchen"))
        }
    }

    @Test fun theWifiAddressIsUsedWhenTheUsbOneDoesNotAnswerAndNoSettingsMeansUnavailable() {
        MockWebServer().use { lan ->
            lan.enqueue(MockResponse().setHeader("Content-Type", "application/json").setBody("""{"ok":true}"""))
            lan.start()
            val usb = "http://127.0.0.1:1/".toHttpUrl() // nothing listens there
            val proxy = RelayPageProxy({ RelayPageProxy.Target(listOf(usb, lan.url("/")), key) })
            assertEquals(200, proxy.handle("GET", "/relay/where", "zone=kitchen").status)
            assertEquals(key, lan.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("X-Key"))
        }
        assertEquals(503, RelayPageProxy({ null }).handle("GET", "/relay/where", "zone=kitchen").status)
        assertFalse(RelayPageProxy.Target(listOf("http://127.0.0.1:8090/".toHttpUrl()), key).toString().contains(key))
    }

    @Test fun theStartAddressIsOnTheAppsOwnOriginWithoutAKey() {
        assertEquals("https://appassets.androidplatform.net/relay/care?zone=kitchen&view=wearer", RelayPageProxy.startUrl("kitchen"))
        assertEquals("https://appassets.androidplatform.net/relay/me?zone=hall", RelayPageProxy.startUrl("hall", page = "/me", view = null))
    }
}
