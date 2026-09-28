import dev.mupyjava.BackendClient;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/** Exercises the real Java/Python connection without opening a window. */
public final class SessionClientSmoke {
    static final LinkedBlockingQueue<BackendClient.Event> events = new LinkedBlockingQueue<>();
    static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    static List<BackendClient.Event> read(String request, String terminal) throws Exception {
        var result = new ArrayList<BackendClient.Event>();
        while (true) {
            var event = events.poll(8, TimeUnit.SECONDS);
            require(event != null, "Timed out waiting for " + terminal);
            if (event.kind().equals("stopped")) continue;
            require(!event.kind().equals("error"), event.text());
            require(event.id().equals(request), "Unexpected request id");
            result.add(event);
            if (event.kind().equals(terminal)) return result;
        }
    }
    static String sessionId(List<BackendClient.Event> batch) {
        return batch.stream().filter(e -> e.kind().equals("session.info")).findFirst().orElseThrow().text();
    }
    static String history(List<BackendClient.Event> batch) {
        return batch.stream().filter(e -> e.kind().equals("history.transcript"))
                .map(BackendClient.Event::text).reduce("", String::concat);
    }
    public static void main(String[] args) throws Exception {
        var repository = Path.of(args[0]);
        var workspace = Path.of(args[1]);
        String copiedId;
        try (var client = new BackendClient(repository, workspace, false, false, null, events::add)) {
            String source = sessionId(read(client.requestHistory(), "history.done"));
            String first = "<html>第一轮\t🌱\ncontinued";
            read(client.send(first), "done");
            read(client.send("original second"), "done");
            var summaries = read(client.requestSessions(), "done").stream()
                    .filter(e -> e.kind().equals("session.item"))
                    .map(e -> BackendClient.parseSessionItem(e.text())).toList();
            require(summaries.size() == 1 && summaries.get(0).sessionId().equals(source), "Catalog mismatch");
            require(summaries.get(0).title().equals("original second"), "Title mismatch");
            var points = read(client.requestPoints(source), "done").stream()
                    .filter(e -> e.kind().equals("session.point"))
                    .map(e -> BackendClient.parseSessionPoint(e.text())).toList();
            require(points.size() == 3 && points.get(1).title().equals(first), "Point text round trip failed");
            var switched = read(client.selectSession(source, points.get(1).pointId()), "done");
            require(switched.get(0).kind().equals("session.reset"), "Missing reset acknowledgement");
            require(history(switched).contains(first) && !history(switched).contains("original second"), "Wrong prefix");
            read(client.send("alternative second"), "done");
            var copied = read(client.forkSession(source, points.get(2).pointId()), "done");
            copiedId = sessionId(copied);
            require(!copiedId.equals(source), "Fork did not create a conversation");
            require(history(copied).contains("original second") && !history(copied).contains("alternative second"), "Wrong fork path");
            summaries = read(client.requestSessions(), "done").stream()
                    .filter(e -> e.kind().equals("session.item"))
                    .map(e -> BackendClient.parseSessionItem(e.text())).toList();
            require(summaries.stream().anyMatch(s -> s.sessionId().equals(copiedId) && s.parentSessionId().equals(source)), "Missing fork origin");
            try { client.selectSession("../outside", points.get(0).pointId()); throw new AssertionError("Invalid id accepted"); }
            catch (IllegalArgumentException expected) { }
            try { BackendClient.parseSessionPoint("v2\tx\ty\tz"); throw new AssertionError("Invalid version accepted"); }
            catch (IllegalArgumentException expected) { }
            try { BackendClient.parseSessionItem("v1\t1-1-1-1-1\ty\tz\t"); throw new AssertionError("Noncanonical id accepted"); }
            catch (IllegalArgumentException expected) { }
        }
        // Backend restart must restore the selected fork, then NEW must acknowledge before clearing.
        try (var client = new BackendClient(repository, workspace, false, false, null, events::add)) {
            var restored = read(client.requestHistory(), "history.done");
            require(sessionId(restored).equals(copiedId), "Restart lost selection");
            require(history(restored).contains("original second"), "Restart lost context");
            var fresh = read(client.newSession(), "done");
            require(fresh.get(0).kind().equals("session.reset") && history(fresh).isEmpty(), "New session reset failed");
            require(!sessionId(fresh).equals(copiedId), "New session id reused");
        }
        System.out.println("Java session catalog/select/fork/restart passed");
    }
}
