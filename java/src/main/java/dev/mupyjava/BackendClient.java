package dev.mupyjava;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Base64;
import java.util.UUID;
import java.util.function.Consumer;

/** Starts the Python engine and exchanges one line per event. */
public final class BackendClient implements AutoCloseable {
    public record Event(String id, String kind, String text) {}
    public record ApprovalRequest(String approvalId, String toolCallId, boolean sessionOption,
                                  String summary, String preview) {}

    private final Process process;
    private final BufferedWriter input;
    private final Thread reader;
    private final Thread errors;

    public BackendClient(Path repository, Path workspace, boolean allowWrite, boolean allowCommand,
                         String ledger, Consumer<Event> onEvent) throws IOException {
        String python = System.getenv().getOrDefault("MU_PYTHON", "python3");
        var command = new java.util.ArrayList<String>();
        command.add(python);
        command.add("-m");
        command.add("mupyjava");
        command.add("--server");
        command.add("--workspace");
        command.add(workspace.toAbsolutePath().toString());
        if (allowWrite) command.add("--allow-write");
        if (allowCommand) command.add("--allow-command");
        if (ledger != null) {
            command.add("--ledger");
            command.add(ledger);
        }
        var builder = new ProcessBuilder(command);
        builder.directory(repository.toFile());
        builder.environment().put("PYTHONPATH", repository.resolve("python").toAbsolutePath().toString());
        process = builder.start();
        input = new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
        reader = new Thread(() -> readEvents(process, onEvent), "mu-backend-events");
        errors = new Thread(() -> readErrors(process, onEvent), "mu-backend-errors");
        reader.setDaemon(true);
        errors.setDaemon(true);
        reader.start();
        errors.start();
    }

    public synchronized String send(String text) throws IOException {
        if (!process.isAlive()) throw new IOException("Python backend has stopped");
        String id = UUID.randomUUID().toString();
        String encoded = Base64.getEncoder().encodeToString(text.getBytes(StandardCharsets.UTF_8));
        input.write("CHAT\t" + id + "\t" + encoded + "\n");
        input.flush();
        return id;
    }

    public synchronized void sendApproval(String approvalId, String answer) throws IOException {
        if (!process.isAlive()) throw new IOException("Python backend has stopped");
        if (!UUID.fromString(approvalId).toString().equals(approvalId))
            throw new IllegalArgumentException("Invalid approval id");
        if (!answer.equals("deny") && !answer.equals("once") && !answer.equals("session"))
            throw new IllegalArgumentException("Invalid approval answer");
        input.write("APPROVAL\t" + approvalId + "\t" + answer + "\n");
        input.flush();
    }

    public synchronized String requestHistory() throws IOException {
        return sendControl("HISTORY");
    }

    public synchronized String newSession() throws IOException {
        return sendControl("NEW");
    }

    private String sendControl(String kind) throws IOException {
        if (!process.isAlive()) throw new IOException("Python backend has stopped");
        String id = UUID.randomUUID().toString();
        input.write(kind + "\t" + id + "\n");
        input.flush();
        return id;
    }

    public static ApprovalRequest parseApproval(String text) {
        String[] fields = text.split("\t", -1);
        if (fields.length != 6 || !fields[0].equals("v1"))
            throw new IllegalArgumentException("Invalid approval request");
        String id = UUID.fromString(fields[1]).toString();
        boolean session;
        if (fields[3].equals("deny,once,session")) session = true;
        else if (fields[3].equals("deny,once")) session = false;
        else throw new IllegalArgumentException("Invalid approval options");
        return new ApprovalRequest(id, decode(fields[2]), session, decode(fields[4]), decode(fields[5]));
    }

    private static String decode(String value) {
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }

    private static void readEvents(Process process, Consumer<Event> onEvent) {
        try (var lines = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = lines.readLine()) != null) {
                String[] parts = line.split("\t", 4);
                if (parts.length != 4 || !parts[0].equals("EVENT")) {
                    onEvent.accept(new Event("", "error", "Invalid backend event"));
                    continue;
                }
                try {
                    String text = new String(Base64.getDecoder().decode(parts[3]), StandardCharsets.UTF_8);
                    onEvent.accept(new Event(parts[1], parts[2], text));
                } catch (IllegalArgumentException error) {
                    onEvent.accept(new Event(parts[1], "error", "Invalid backend text"));
                }
            }
            onEvent.accept(new Event("", "stopped", "Python backend stopped"));
        } catch (IOException error) {
            onEvent.accept(new Event("", "error", error.getMessage()));
        }
    }

    private static void readErrors(Process process, Consumer<Event> onEvent) {
        try (var lines = new BufferedReader(new InputStreamReader(process.getErrorStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = lines.readLine()) != null) onEvent.accept(new Event("", "error", line));
        } catch (IOException error) {
            onEvent.accept(new Event("", "error", error.getMessage()));
        }
    }

    @Override
    public void close() {
        process.destroy();
    }
}
