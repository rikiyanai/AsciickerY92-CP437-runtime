import { Server } from "@colyseus/core";
import { WebSocketTransport } from "@colyseus/ws-transport";
import { AuthoritativeRoom } from "./rooms/AuthoritativeRoom.js";

const port = Number.parseInt(process.env.COLYSEUS_PORT ?? "2567", 10);
const gameServer = new Server({
  transport: new WebSocketTransport(),
});

gameServer.define("asciicker_spike", AuthoritativeRoom);

await gameServer.listen(port);
console.log(`ASCIICKER_COLYSEUS_SPIKE_LISTENING port=${port}`);
