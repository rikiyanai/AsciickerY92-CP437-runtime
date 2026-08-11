var audio_port = null;
var audio_call = 0;

var Init = Module.cwrap('Init', 'number', ['number']);
var Proc = Module.cwrap('Proc', 'number', []);
var Call = Module.cwrap('Call', null, ['number','number']);
var XOgg = Module.cwrap('XOgg', null, ['number','number','number']);
var AudioDebugStateJson = Module.cwrap('AudioDebugStateJson', 'string', []);

class AsciickerAudio extends AudioWorkletProcessor 
{
    constructor (...args) 
    {
        super(...args);

        audio_port = this.port;

        audio_port.onmessage = (e) => 
        {
            this.cmd_calls++;
            if (e.data.length <= 4096)
            {
                Module.HEAPU8.set(e.data, audio_call)
                Call(audio_call, e.data.length);
            }
            else
            {
                let addr = Module._malloc(e.data.length);
                Module.HEAPU8.set(e.data, addr)
                Call(addr, e.data.length);
                Module._free(addr);
            }
            try {
                audio_port.postMessage({ak_audio_diag: {
                    kind: "cmd",
                    cmd_calls: this.cmd_calls,
                    state: AudioDebugStateJson()
                }});
            } catch (err) {}
        }
        this.cmd_calls = 0;
        this.proc_calls = 0;
        this.nonzero_proc = 0;
        this.last_peak = 0;

        const c = args[0].processorOptions;
        let max_size = 0;
        let num = c.length;
        for (const s in c)
            max_size = max_size < c[s].length ? c[s].length : max_size;

        audio_call = Init(num);

        let data = 0;
        if (max_size)
            data = Module._malloc(max_size);

        for (const s in c)
        {
            if (c[s].length)
                Module.HEAPU8.set(c[s], data);
            XOgg(s, data, c[s].length);
        }

        if (max_size)
            Module._free(data);

        //audio_port.postMessage("Audio Initialized ");
    }

    process (inputs, outputs, parameters) 
    {
        let left = outputs[0][0];
        let right = outputs[0][1];

        let len = 128;

        let ptr = Proc();

        let heap = Module.HEAP16;
        const norm = 1.0/32767.0;
        let peak = 0;
        for (let i=0,j=ptr>>1; i<len; i++,j+=2)
        {
            const l = heap[j+0];
            const r = heap[j+1];
            left[i] = l * norm;
            right[i] = r * norm;
            const al = Math.abs(l);
            const ar = Math.abs(r);
            if (al > peak) peak = al;
            if (ar > peak) peak = ar;
        }
        this.proc_calls++;
        this.last_peak = peak;
        if (peak > 0)
            this.nonzero_proc++;
        if (peak > 0 || (this.proc_calls % 120) === 0) {
            try {
                audio_port.postMessage({ak_audio_diag: {
                    kind: "proc",
                    proc_calls: this.proc_calls,
                    nonzero_proc: this.nonzero_proc,
                    last_peak: this.last_peak,
                    state: AudioDebugStateJson()
                }});
            } catch (err) {}
        }

        return true;
    }
}
  
registerProcessor('asciicker-audio', AsciickerAudio);
