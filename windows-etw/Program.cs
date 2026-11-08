using System.Diagnostics;
using System.Text.Json;
using Microsoft.Diagnostics.Tracing.Parsers;
using Microsoft.Diagnostics.Tracing.Session;

if (!OperatingSystem.IsWindows() || !TraceEventSession.IsElevated().GetValueOrDefault())
{
    Console.Error.WriteLine("Run this collector on Windows from an administrator terminal.");
    return 1;
}

const double MiB = 1024 * 1024;
string directory = Path.Combine(
    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TuringIonTelemetry");
Directory.CreateDirectory(directory);
string destination = Path.Combine(directory, "io.json");
string temporary = Path.Combine(directory, $"io-{Environment.ProcessId}.tmp");
object gate = new();
Dictionary<int, (long Disk, long Net)> totals = new();
long previous = Stopwatch.GetTimestamp();
using var stop = new CancellationTokenSource();
using var session = new TraceEventSession($"TuringIonIO-{Environment.ProcessId}");
ConsoleCancelEventHandler onCancel = (_, args) => { args.Cancel = true; stop.Cancel(); };
Console.CancelKeyPress += onCancel;

void Record(int pid, int bytes, bool disk)
{
    if (pid <= 0 || bytes <= 0) return;
    lock (gate)
    {
        totals.TryGetValue(pid, out var old);
        totals[pid] = disk ? (old.Disk + bytes, old.Net) : (old.Disk, old.Net + bytes);
    }
}

var kernel = session.Source.Kernel;
kernel.DiskIORead += data => Record(data.ProcessID, data.TransferSize, true);
kernel.DiskIOWrite += data => Record(data.ProcessID, data.TransferSize, true);
kernel.TcpIpSend += data => Record(data.ProcessID, data.size, false);
kernel.TcpIpRecv += data => Record(data.ProcessID, data.size, false);
kernel.TcpIpSendIPV6 += data => Record(data.ProcessID, data.size, false);
kernel.TcpIpRecvIPV6 += data => Record(data.ProcessID, data.size, false);
kernel.UdpIpSend += data => Record(data.ProcessID, data.size, false);
kernel.UdpIpRecv += data => Record(data.ProcessID, data.size, false);
kernel.UdpIpSendIPV6 += data => Record(data.ProcessID, data.size, false);
kernel.UdpIpRecvIPV6 += data => Record(data.ProcessID, data.size, false);

try
{
    session.EnableKernelProvider(KernelTraceEventParser.Keywords.DiskIO
        | KernelTraceEventParser.Keywords.DiskIOInit | KernelTraceEventParser.Keywords.NetworkTCPIP
        | KernelTraceEventParser.Keywords.Process | KernelTraceEventParser.Keywords.Thread);
    using var registration = stop.Token.Register(() => session.Source.StopProcessing());
    var writer = Task.Run(async () =>
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(4));
        try
        {
            while (await timer.WaitForNextTickAsync(stop.Token))
            {
                Dictionary<int, (long Disk, long Net)> snapshot;
                double elapsed;
                lock (gate)
                {
                    long now = Stopwatch.GetTimestamp();
                    elapsed = Stopwatch.GetElapsedTime(previous, now).TotalSeconds;
                    previous = now;
                    snapshot = totals;
                    totals = new();
                }
                var rates = snapshot.ToDictionary(x => x.Key,
                    x => new[] { x.Value.Disk / MiB / elapsed, x.Value.Net / MiB / elapsed });
                try
                {
                    File.WriteAllText(temporary, JsonSerializer.Serialize(new
                    {
                        timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
                        rates
                    }));
                    File.Move(temporary, destination, true);
                }
                catch (IOException ex)
                {
                    Console.Error.WriteLine($"Could not publish counters: {ex.Message}");
                }
            }
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) {}
    });
    Console.WriteLine("Collecting Windows disk and network counts. Press Ctrl+C to stop.");
    try { session.Source.Process(); }
    finally
    {
        stop.Cancel();
        await writer;
    }
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine($"ETW session unavailable: {ex.Message}");
    return 1;
}
finally
{
    Console.CancelKeyPress -= onCancel;
    try { File.Delete(temporary); } catch (IOException) {}
}
